"""内容解析器业务规则：状态流转、扩展字段写入、版本治理与跨模块裁决口径。

解析模板是字段配置的主配置：扩展字段（正则模式、默认取值、字段类型、必填标识、
清洗函数）随登记/编辑完整落库，每次编辑产生可追溯的新版本，启用动作把当前版本
标记为“已确认版本”，供格式归一引用。
"""
from __future__ import annotations

from typing import Any

from app.services.versioning import (
    PARSER_DEFAULTS,
    PARSER_EDITABLE,
    PARSER_REQUIRED,
    ConflictError,
    VersionedService,
)
from app.store import store

MODULE = "parser"
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用模板": "已启用", "停用模板": "已停用"}
NEGATIVE_ACTIONS = ["停用模板"]

# 向后兼容：部分路由/测试仍按旧名引用
REQUIRED_FIELDS = PARSER_REQUIRED


class ParserService(VersionedService):
    module = MODULE
    required_fields = PARSER_REQUIRED
    editable_fields = PARSER_EDITABLE
    field_defaults = PARSER_DEFAULTS

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("模板编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        # 视图层补齐旧记录缺字段，列表也能完整读回扩展字段
        return [self.decorate(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self.decorate(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = self.check_required(values)
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = self.build_entry(self._normalize(values), rows)
        rows.append(entry)
        return self.decorate(entry), []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        entry, missing = super().update_entry(entry_id, self._normalize(values))
        if entry is None:
            return None, missing
        return self.decorate(entry), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"解析模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于内容解析器可执行范围"
        self.check_action_version(entry, values or {})
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 启用即确认：当前版本成为最新已确认版本；历史确认标记保留（曾经确认的版本
        # 仍可被归一规则引用），保证引用方钉住的版本不会失效
        if action == "启用模板":
            is_legacy = "version" not in entry
            if is_legacy:
                # 旧记录首次启用：补 v1 快照与版本链后再确认，兼容旧数据
                entry["version"] = 1
                entry["confirmed_version"] = None
                entry["revisions"] = [self._make_revision(entry, note="旧记录补齐")]
            entry["confirmed_version"] = entry.get("version", 1)
            for revision in entry.get("revisions", []):
                if revision["version"] == entry["confirmed_version"]:
                    revision["confirmed"] = True
        return self.decorate(entry), f"解析模板已{action}"

    def list_revisions(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return super().list_revisions(entry_id)

    # -- 供格式归一引用的主配置查询 ---------------------------------------

    def find_by_code(self, template_code: str) -> dict[str, Any] | None:
        for row in store.rows(MODULE):
            if str(row.get("模板编号", "")) == str(template_code):
                return row
        return None

    def get_confirmed_revision(
        self, template_code: str, version: int
    ) -> tuple[dict[str, Any] | None, str]:
        """返回指定解析模板某个版本的已确认快照；不可引用时给出可读原因。"""
        entry = self.find_by_code(template_code)
        if entry is None:
            return None, f"解析模板「{template_code}」不存在"
        # 旧记录兼容：版本治理上线前已启用的模板，视同 v1 已确认，用当前数据合成快照
        if "version" not in entry:
            if int(version) != 1 or entry.get("status") != "已启用":
                return None, (
                    f"解析模板「{template_code}」为旧记录且未启用，"
                    "请先在内容解析器完成编辑并启用后再引用"
                )
            return {
                "version": 1,
                "confirmed": True,
                "note": "旧记录补齐",
                "snapshot": {field: entry.get(field) for field in self.editable_fields},
            }, ""
        current_version = entry.get("version", 1)
        if int(version) < 1 or int(version) > int(current_version):
            return None, f"解析模板「{template_code}」的 v{version} 不存在，当前最新版本为 v{current_version}"
        target_revision = next(
            (rev for rev in entry.get("revisions", []) if int(rev["version"]) == int(version)),
            None,
        )
        if target_revision is None:
            return None, f"解析模板「{template_code}」的 v{version} 不存在"
        confirmed_version = entry.get("confirmed_version")
        if confirmed_version != int(version):
            return None, (
                f"解析模板「{template_code}」的 v{version} 尚未确认"
                f"（当前已确认版本：{('v' + str(confirmed_version)) if confirmed_version else '无'}），"
                "归一规则只能引用已确认的字段版本"
            )
        return target_revision, ""

    # -- 内部工具 ----------------------------------------------------------

    def _normalize(self, values: dict[str, Any]) -> dict[str, Any]:
        """归一化布尔类扩展字段；expected_version 与服务端字段由基类按声明过滤。"""
        normalized = dict(values)
        if "必填标识" in normalized and normalized["必填标识"] is not None:
            flag = normalized["必填标识"]
            if isinstance(flag, str):
                normalized["必填标识"] = flag.strip() in {"true", "1", "是", "必填", "Y", "y"}
            else:
                normalized["必填标识"] = bool(flag)
        return normalized


parser_service = ParserService()
