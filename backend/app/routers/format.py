"""格式归一接口：维护转换规则，覆盖登记、扩展字段编辑、版本历史与状态动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.format import format_service
from app.services.versioning import BadRequestError, ConflictError

router = APIRouter(prefix="/api/format", tags=["格式归一"])

service = format_service

LIST_FIELDS = ["规则编号", "输入字段", "输出字段", "转换函数", "默认取值", "异常策略", "格式化模板", "校验正则"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def _raise_conflict(error: ConflictError) -> HTTPException:
    detail: dict[str, Any] = {"ok": False, "message": error.message}
    if error.current_version is not None:
        detail["current_version"] = error.current_version
    if error.conflict is not None:
        detail["conflict"] = error.conflict
    return HTTPException(status_code=409, detail=detail)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤格式归一列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出格式归一清单：返回当前过滤条件下的全量数据（含扩展字段）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "format", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条转换规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"转换规则 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/revisions", response_model=PageResult[dict])
def get_revisions(entry_id: int) -> PageResult[dict]:
    """读取转换规则的版本历史，每次扩展字段编辑都有可追溯快照。"""
    revisions = service.list_revisions(entry_id)
    if revisions is None:
        raise HTTPException(status_code=404, detail=f"转换规则 {entry_id} 不存在或已归档")
    return PageResult(items=revisions, total=len(revisions), page=1, size=len(revisions) or 1)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条转换规则，必填字段与扩展字段都会完整写入；引用未确认解析版本会被阻断。"""
    try:
        entry, missing = service.create_entry(payload.values)
    except ConflictError as error:
        raise _raise_conflict(error) from error
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="转换规则已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """编辑转换规则的可修改扩展字段；id、状态与版本链由服务端维护，拒绝旧版本提交。"""
    try:
        entry, missing = service.update_entry(entry_id, payload.values)
    except BadRequestError as error:
        raise HTTPException(status_code=400, detail=error.message) from error
    except ConflictError as error:
        raise _raise_conflict(error) from error
    if entry is None:
        raise HTTPException(status_code=404, detail=f"转换规则 {entry_id} 不存在或已归档")
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="转换规则扩展字段已更新", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条转换规则执行提交校验、启用规则、停用规则；冲突裁决失败会阻断并给出双方来源。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, payload.values)
    except ConflictError as error:
        raise _raise_conflict(error) from error
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
