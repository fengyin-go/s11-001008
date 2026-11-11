"""扩展字段与乐观版本控制的公共规则。

两个业务模块（内容解析器、格式归一）共用同一套口径：

- ``REQUIRED_FIELDS``：登记时必填的业务字段；
- ``EDITABLE_FIELDS``：允许页面登记/编辑的扩展字段，登记时整组写入，不再只复制必填字段；
- ``SERVER_FIELDS``：由服务端维护的字段，用户提交里即使携带也一律忽略，防止覆盖 id 或业务状态；
- ``FIELD_DEFAULTS``：旧记录缺字段时的视图层补齐默认值。注意区分“字段缺失”与
  “默认取值为 0/空串”——只有缺失才补齐，用户显式给出的 0、False、空串都会原样保留。
"""
from __future__ import annotations

from typing import Any

# 服务端维护字段：禁止页面写入，避免用户覆盖 id、业务状态或版本链
SERVER_FIELDS = frozenset(
    ["id", "status", "pending", "abnormal", "version", "confirmed_version",
     "revisions", "legacy", "created_at", "updated_at"]
)

# 解析模板的字段声明：必填 + 可编辑扩展字段
PARSER_REQUIRED = ["模板编号", "目标字段名", "XPath表达式"]
PARSER_EDITABLE = [
    "模板编号", "目标字段名", "XPath表达式",
    "正则模式", "默认取值", "字段类型", "必填标识", "清洗函数",
]
PARSER_DEFAULTS: dict[str, Any] = {
    "正则模式": "",
    "默认取值": None,
    "字段类型": "string",
    "必填标识": False,
    "清洗函数": "",
}

# 格式归一的字段声明
FORMAT_REQUIRED = ["规则编号", "输入字段", "输出字段"]
FORMAT_EDITABLE = [
    "规则编号", "输入字段", "输出字段",
    "转换函数", "默认取值", "异常策略", "格式化模板", "校验正则",
    "解析模板编号", "引用解析版本",
]
FORMAT_DEFAULTS: dict[str, Any] = {
    "转换函数": "",
    "默认取值": None,
    "异常策略": "保留原值",
    "格式化模板": "",
    "校验正则": "",
    "解析模板编号": None,
    "引用解析版本": None,
}


class ConflictError(Exception):
    """业务冲突（含乐观锁过期、跨模块引用/裁决冲突），由路由层转成 409。"""

    def __init__(
        self,
        message: str,
        *,
        current_version: int | None = None,
        conflict: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.current_version = current_version
        self.conflict = conflict


class BadRequestError(Exception):
    """请求本身不合法（如缺少乐观锁版本号），由路由层转成 400。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class VersionedService:
    """给 service 提供扩展字段写入、旧记录补齐与乐观版本控制的共用实现。"""

    module: str
    required_fields: list[str]
    editable_fields: list[str]
    field_defaults: dict[str, Any]

    # -- 视图层补齐 --------------------------------------------------------

    def decorate(self, entry: dict[str, Any]) -> dict[str, Any]:
        """读取时补齐旧记录缺失的扩展字段与版本信息，不改动仓库里的原始记录。"""
        view = dict(entry)
        for field, default in self.field_defaults.items():
            if field not in view:
                view[field] = default
        view.setdefault("version", 1)
        # 旧记录兼容：版本治理上线前已启用的记录，视同 v1 已确认（仅视图层推导，不改写仓库）
        if "confirmed_version" not in view:
            view["confirmed_version"] = 1 if view.get("status") == "已启用" else None
        view.setdefault("revisions", [])
        # 旧记录的判定依据是仓库原记录里没有版本链；补齐结果只存在于视图
        view["legacy"] = "version" not in entry
        return view

    # -- 登记 --------------------------------------------------------------

    def check_required(self, values: dict[str, Any]) -> list[str]:
        # 0、False 是合法显式值，不能当作“缺失”；只有 None、空白串才算缺填
        missing: list[str] = []
        for field in self.required_fields:
            value = values.get(field)
            if value is None:
                missing.append(field)
            elif isinstance(value, str) and not value.strip():
                missing.append(field)
        return missing

    def build_entry(self, values: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        """登记新记录：写入全部已声明字段，服务端字段由这里统一维护。"""
        entry: dict[str, Any] = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "status": "草稿",
            "pending": True,
            "abnormal": False,
            "version": 1,
            "confirmed_version": None,
            "revisions": [],
        }
        self._apply_editable(entry, values)
        entry["revisions"] = [self._make_revision(entry, note="登记创建")]
        return entry

    def _apply_editable(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        """只复制已声明的可编辑字段；键不在声明内或属于服务端字段一律忽略。"""
        for field in self.editable_fields:
            if field in SERVER_FIELDS:
                # 双保险：即使字段声明误收了服务端字段，也不允许页面写入
                continue
            if field in values:
                entry[field] = values[field]

    # -- 编辑（乐观锁） ----------------------------------------------------

    def update_entry(
        self, entry_id: int, values: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        entry = self._find_raw(entry_id)
        if entry is None:
            return None, []
        if "expected_version" not in values:
            raise BadRequestError(
                "编辑扩展字段必须携带 expected_version，防止旧版本覆盖新修改"
            )
        expected = values.get("expected_version")
        current = entry.get("version", 1)
        if int(expected) != int(current):
            raise ConflictError(
                f"数据已被其他人修改：当前版本为 v{current}，提交基于 v{expected}，请刷新后重试",
                current_version=int(current),
            )

        # 先在副本上完成全部变更，成功后才整体替换，保证拒绝时不留半份修改
        is_legacy = "version" not in entry
        candidate = dict(entry)
        self._apply_editable(candidate, values)
        candidate["version"] = int(current) + 1
        candidate["legacy"] = False
        existing_revisions = list(entry.get("revisions", []))
        if is_legacy:
            # 旧记录首次编辑：用编辑前的数据补一份 v1 快照，历史不中断
            legacy_snapshot = self._make_revision(
                {**entry, "version": 1, "confirmed_version": entry.get("confirmed_version")},
                note="旧记录补齐",
            )
            existing_revisions = [legacy_snapshot, *existing_revisions]
        candidate["revisions"] = existing_revisions
        candidate["revisions"].append(
            self._make_revision(candidate, note="编辑扩展字段")
        )
        entry.clear()
        entry.update(candidate)
        return entry, []

    def check_action_version(self, entry: dict[str, Any], values: dict[str, Any]) -> None:
        """动作接口同样校验乐观锁，避免拿着旧版本推动状态流转。"""
        if "expected_version" not in values:
            return
        current = entry.get("version", 1)
        if int(values["expected_version"]) != int(current):
            raise ConflictError(
                f"数据已被其他人修改：当前版本为 v{current}，提交基于 v{values['expected_version']}，请刷新后重试",
                current_version=int(current),
            )

    # -- 版本历史 ----------------------------------------------------------

    def list_revisions(self, entry_id: int) -> list[dict[str, Any]] | None:
        entry = self._find_raw(entry_id)
        if entry is None:
            return None
        if "revisions" in entry:
            return list(entry["revisions"])
        # 旧记录没有版本链：用当前数据补一份只含可编辑字段的 v1 快照，保证历史接口可读
        return [{
            "version": 1,
            "confirmed": entry.get("status") in ("已启用",),
            "note": "旧记录补齐",
            "snapshot": {field: entry.get(field) for field in self.editable_fields},
        }]

    # -- 内部工具 ----------------------------------------------------------

    def _find_raw(self, entry_id: int) -> dict[str, Any] | None:
        from app.store import store

        return store.find(self.module, entry_id)

    def _make_revision(self, entry: dict[str, Any], *, note: str) -> dict[str, Any]:
        from app.store import store

        snapshot = store.snapshot(
            {field: entry.get(field) for field in self.editable_fields}
        )
        return {
            "version": entry["version"],
            "confirmed": entry.get("confirmed_version") == entry["version"],
            "note": note,
            "snapshot": snapshot,
        }
