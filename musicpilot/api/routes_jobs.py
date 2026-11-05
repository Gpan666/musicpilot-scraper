from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import JobCreate
from musicpilot.domain.models import JobKind
from musicpilot.domain.summary import detail_payload
from musicpilot.domain.transfer import mode_label
from musicpilot.services.jobs import JobConflict
from musicpilot.services.scraper import JobRequest

router = APIRouter(prefix="/jobs", tags=["jobs"])

STATUS_LABELS = {
    "planned": "计划中",
    "done": "成功",
    "skipped": "跳过",
    "failed": "失败",
    "needs_review": "需确认",
}


def _job_payload(context: AppContext, job: dict[str, Any]) -> dict[str, Any]:
    """Attach the display name so the UI never has to show a raw job id."""
    return {**job, "label": context.jobs.label(str(job.get("id") or ""))}


def _item_payload(item: dict[str, Any]) -> dict[str, Any]:
    """中文方式名 + 中文摘要；完整原文仍放在 ``detail`` 里。"""
    return detail_payload({**item, "mode_label": mode_label(str(item.get("mode") or ""))})


@router.post("", status_code=202)
async def create_job(
    payload: JobCreate,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    kind = JobKind(payload.kind)
    dry_run = payload.dry_run if payload.dry_run is not None else kind is JobKind.PREVIEW
    request = JobRequest(
        kind=kind,
        dry_run=dry_run,
        paths=payload.paths or None,
        organize=payload.organize,
        skip_low_confidence=payload.skip_low_confidence,
    )
    paths = payload.paths or []
    choices = (
        {}
        if dry_run
        else {path: item.model_dump() for path, item in payload.choices.items()}
    )
    title = Path(paths[0]).name if len(paths) == 1 else ""
    try:
        job = context.jobs.start(request, choices, title=title)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_payload(context, job.as_dict())


@router.get("")
def list_jobs(
    limit: int = 30,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    rows = context.jobs.list(max(1, min(limit, 100)))
    return {"jobs": [_job_payload(context, row) for row in rows]}


@router.get("/{job_id}")
def get_job(job_id: str, context: AppContext = Depends(get_context)) -> dict[str, Any]:
    job = context.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "job": _job_payload(context, job),
        "items": [_item_payload(row) for row in context.jobs.items(job_id, limit=50)],
    }


@router.get("/{job_id}/items")
def get_items(
    job_id: str,
    limit: int = 500,
    offset: int = 0,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    rows = context.jobs.items(
        job_id, limit=max(1, min(limit, 2000)), offset=max(0, offset)
    )
    return {"items": [_item_payload(row) for row in rows]}


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, context: AppContext = Depends(get_context)) -> dict[str, Any]:
    if not context.jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return {"ok": True, "message": "已请求取消，当前文件处理完成后停止"}


@router.delete("/{job_id}")
def delete_job(job_id: str, context: AppContext = Depends(get_context)) -> dict[str, Any]:
    try:
        removed = context.jobs.delete(job_id)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "message": "任务记录已删除"}


@router.delete("")
def clear_jobs(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    removed = context.jobs.clear()
    return {"ok": True, "removed": removed, "message": f"已清空 {removed} 条任务记录"}


@router.get("/{job_id}/report")
def report(job_id: str, context: AppContext = Depends(get_context)) -> Response:
    if context.jobs.get(job_id) is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    items = context.jobs.items(job_id, limit=2000)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["源文件", "目标路径", "转移方式", "状态", "置信度", "元数据源", "标题", "歌手", "专辑", "说明"]
    )
    for item in items:
        writer.writerow(
            [
                item["source"],
                item["target"],
                mode_label(str(item.get("mode") or "")),
                STATUS_LABELS.get(str(item.get("status") or ""), ""),
                f"{item['confidence']:.2f}",
                item["source_name"],
                item["title"],
                item["artist"] or "",
                item["album"] or "",
                item.get("detail_brief") or item["detail"],
            ]
        )
    content = "\ufeff" + buffer.getvalue()  # Excel-friendly UTF-8 BOM
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="musicpilot-{job_id}.csv"'},
    )
