"""内容解析器接口：维护解析模板，覆盖登记、扩展字段编辑、版本历史与状态动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.parser import parser_service
from app.services.versioning import BadRequestError, ConflictError

router = APIRouter(prefix="/api/parser", tags=["内容解析器"])

service = parser_service

LIST_FIELDS = ["模板编号", "目标字段名", "XPath表达式", "正则模式", "默认取值", "字段类型", "必填标识", "清洗函数"]
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
    keyword: str | None = Query(default=None, description="按模板编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按模板编号与状态过滤内容解析器列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出内容解析器清单：返回当前过滤条件下的全量数据（含扩展字段）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "parser", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条解析模板明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"解析模板 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/revisions", response_model=PageResult[dict])
def get_revisions(entry_id: int) -> PageResult[dict]:
    """读取解析模板的版本历史，每次扩展字段编辑都有可追溯快照。"""
    revisions = service.list_revisions(entry_id)
    if revisions is None:
        raise HTTPException(status_code=404, detail=f"解析模板 {entry_id} 不存在或已归档")
    return PageResult(items=revisions, total=len(revisions), page=1, size=len(revisions) or 1)


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条解析模板，必填字段与扩展字段都会完整写入；缺必填字段时说明原因。"""
    try:
        entry, missing = service.create_entry(payload.values)
    except ConflictError as error:
        raise _raise_conflict(error) from error
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="解析模板已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """编辑解析模板的可修改扩展字段；id、状态与版本链由服务端维护，拒绝旧版本提交。"""
    try:
        entry, missing = service.update_entry(entry_id, payload.values)
    except BadRequestError as error:
        raise HTTPException(status_code=400, detail=error.message) from error
    except ConflictError as error:
        raise _raise_conflict(error) from error
    if entry is None:
        raise HTTPException(status_code=404, detail=f"解析模板 {entry_id} 不存在或已归档")
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="解析模板扩展字段已更新", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条解析模板执行提交校验、启用模板、停用模板；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    try:
        entry, message = service.run_action(entry_id, action, payload.values)
    except ConflictError as error:
        raise _raise_conflict(error) from error
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
