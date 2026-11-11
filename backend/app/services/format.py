"""格式归一业务规则：扩展字段写入、版本治理与跨模块冲突裁决。

裁决口径（确定性规则，解析与归一不会相互覆盖）：

- 解析模板是主配置：归一规则通过「解析模板编号 + 引用解析版本」引用解析侧某个
  **已确认** 的字段版本，未确认或不存在的版本一律阻断登记；
- 归一规则提交校验时，对同一目标字段逐项比对双方的默认取值、清洗/转换函数与
  必填约束，出现冲突则阻断确认，并在响应里同时给出解析来源与归一来源；
- 解析模板后续出新版本不影响已登记的归一规则——归一丝毫钉在已确认的旧版本上。
"""
from __future__ import annotations

from typing import Any

from app.services.parser import parser_service
from app.services.versioning import (
    FORMAT_DEFAULTS,
    FORMAT_EDITABLE,
    FORMAT_REQUIRED,
    ConflictError,
    VersionedService,
)
from app.store import store

MODULE = "format"
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]

# 向后兼容：部分路由/测试仍按旧名引用
REQUIRED_FIELDS = FORMAT_REQUIRED

# 进入确认环节前必须通过冲突裁决的动作
CONFIRM_ACTIONS = {"提交校验", "启用规则"}


class FormatService(VersionedService):
    module = MODULE
    required_fields = FORMAT_REQUIRED
    editable_fields = FORMAT_EDITABLE
    field_defaults = FORMAT_DEFAULTS

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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self.decorate(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self.decorate(entry) if entry is not None else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = self.check_required(values)
        if missing:
            return None, missing
        # 显式引用解析模板时，登记阶段就必须指向已确认版本（草稿允许先保存不带引用）
        self._validate_reference(values)
        rows = store.rows(MODULE)
        entry = self.build_entry(self._normalize(values), rows)
        rows.append(entry)
        return self.decorate(entry), []

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, []
        merged = self.decorate(entry)
        merged.update({k: v for k, v in values.items() if k != "expected_version"})
        self._validate_reference(merged)
        updated, missing = super().update_entry(entry_id, self._normalize(values))
        if updated is None:
            return None, missing
        return self.decorate(updated), []

    def run_action(
        self, entry_id: int, action: str, values: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"转换规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于格式归一可执行范围"
        self.check_action_version(entry, values or {})
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action in CONFIRM_ACTIONS:
            self._guard_conflict(entry)

        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return self.decorate(entry), f"转换规则已{action}"

    def list_revisions(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        return super().list_revisions(entry_id)

    # -- 引用校验 ----------------------------------------------------------

    def _validate_reference(self, values: dict[str, Any]) -> None:
        """归一只能引用已确认的解析字段版本；未确认/不存在一律阻断。"""
        template_code = values.get("解析模板编号")
        ref_version = values.get("引用解析版本")
        if not template_code and not ref_version:
            return
        if not template_code or not ref_version:
            raise ConflictError("引用解析模板时必须同时提供「解析模板编号」和「引用解析版本」")
        _revision, reason = parser_service.get_confirmed_revision(
            str(template_code), int(ref_version)
        )
        if reason:
            raise ConflictError(reason)

    # -- 冲突裁决 ----------------------------------------------------------

    def _guard_conflict(self, entry: dict[str, Any]) -> None:
        view = self.decorate(entry)
        target_field = str(view.get("输出字段") or "").strip()
        if not target_field:
            return

        revision = self._resolve_parser_revision(view)
        if revision is None:
            return
        parser_snapshot = revision["snapshot"]
        parser_target = str(parser_snapshot.get("目标字段名") or "").strip()
        if parser_target and parser_target != target_field:
            raise ConflictError(
                "目标字段与解析模板声明不一致，确认已阻断",
                conflict=self._conflict_payload(view, revision, ["目标字段名与解析模板的目标字段不一致"]),
            )

        reasons: list[str] = []
        # 1. 默认取值：None 视为未声明不参与比对；0、空串等显式值必须区分对待
        parser_default = parser_snapshot.get("默认取值")
        format_default = view.get("默认取值")
        if parser_default is not None and format_default is not None:
            if str(parser_default) != str(format_default):
                reasons.append(
                    f"默认取值冲突：解析模板为「{parser_default}」，归一规则为「{format_default}」"
                )

        # 2. 清洗/转换函数：归一的转换函数不得改写解析侧已确认的清洗口径
        parser_cleaner = str(parser_snapshot.get("清洗函数") or "").strip()
        format_transformer = str(view.get("转换函数") or "").strip()
        if parser_cleaner and format_transformer and parser_cleaner != format_transformer:
            reasons.append(
                f"函数口径冲突：解析模板清洗函数为「{parser_cleaner}」，归一转换函数为「{format_transformer}」"
            )

        # 3. 必填约束：解析侧要求必填时，归一的异常策略不允许把值置空
        if parser_snapshot.get("必填标识") is True:
            strategy = str(view.get("异常策略") or "")
            if "置空" in strategy or "丢弃" in strategy:
                reasons.append(
                    f"必填约束冲突：解析模板要求必填，归一异常策略「{strategy}」会产出空值"
                )

        if reasons:
            raise ConflictError(
                "归一规则与解析模板的已确认字段版本冲突，确认已阻断；以解析模板为主配置，请调整归一规则后重试",
                conflict=self._conflict_payload(view, revision, reasons),
            )

    def _resolve_parser_revision(self, view: dict[str, Any]) -> dict[str, Any] | None:
        template_code = view.get("解析模板编号")
        ref_version = view.get("引用解析版本")
        if template_code and ref_version:
            revision, _reason = parser_service.get_confirmed_revision(
                str(template_code), int(ref_version)
            )
            return revision
        # 未显式引用时，按目标字段匹配已启用（含已确认版本）的解析模板，保证裁决确定可解释
        target_field = str(view.get("输出字段") or "").strip()
        for parser_row in store.rows(parser_service.module):
            if str(parser_row.get("目标字段名") or "").strip() != target_field:
                continue
            # 旧记录兼容：无版本链的已启用模板视同 v1 已确认
            if "version" not in parser_row:
                if parser_row.get("status") != "已启用":
                    continue
                revision, _reason = parser_service.get_confirmed_revision(
                    str(parser_row.get("模板编号")), 1
                )
            else:
                confirmed_version = parser_row.get("confirmed_version")
                if not confirmed_version:
                    continue
                revision, _reason = parser_service.get_confirmed_revision(
                    str(parser_row.get("模板编号")), int(confirmed_version)
                )
            if revision is not None:
                return revision
        return None

    def _conflict_payload(
        self, view: dict[str, Any], revision: dict[str, Any], reasons: list[str]
    ) -> dict[str, Any]:
        parser_entry = parser_service.find_by_code(view.get("解析模板编号") or "")
        parser_snapshot = revision["snapshot"]
        return {
            "target_field": view.get("输出字段"),
            "reasons": reasons,
            "resolution": "解析模板为主配置，归一规则必须对齐已确认的解析字段版本",
            "parser_source": {
                "模块": "parser",
                "模板编号": parser_snapshot.get("模板编号")
                or (parser_entry or {}).get("模板编号"),
                "目标字段名": parser_snapshot.get("目标字段名"),
                "version": revision["version"],
                "默认取值": parser_snapshot.get("默认取值"),
                "清洗函数": parser_snapshot.get("清洗函数"),
                "必填标识": parser_snapshot.get("必填标识"),
            },
            "format_source": {
                "模块": "format",
                "规则编号": view.get("规则编号"),
                "输出字段": view.get("输出字段"),
                "默认取值": view.get("默认取值"),
                "转换函数": view.get("转换函数"),
                "异常策略": view.get("异常策略"),
            },
        }

    # -- 内部工具 ----------------------------------------------------------

    def _normalize(self, values: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(values)
        ref_version = normalized.get("引用解析版本")
        if isinstance(ref_version, str) and ref_version.strip():
            try:
                normalized["引用解析版本"] = int(ref_version)
            except ValueError as error:
                raise ConflictError("引用解析版本必须是整数版本号") from error
        return normalized


format_service = FormatService()
