from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import JobCreate
from musicpilot.domain.models import JobKind
from musicpilot.services.jobs import JobConflict
from musicpilot.services.scraper import JobRequest

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
    choices = (
        {}
        if dry_run
        else {path: item.model_dump() for path, item in payload.choices.items()}
    )
    try:
        job = context.jobs.start(request, choices)
    except JobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.as_dict()


@router.get("")
def list_jobs(
    limit: int = 30,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    return {"jobs": context.jobs.list(max(1, min(limit, 100)))}


@router.get("/{job_id}")
def get_job(job_id: str, context: AppContext = Depends(get_context)) -> dict[str, Any]:
    job = context.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"job": job, "items": context.jobs.items(job_id, limit=50)}


@router.get("/{job_id}/items")
def get_items(
    job_id: str,
    limit: int = 500,
    offset: int = 0,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    return {
        "items": context.jobs.items(
            job_id, limit=max(1, min(limit, 2000)), offset=max(0, offset)
        )
    }


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, context: AppContext = Depends(get_context)) -> dict[str, Any]:
    if not context.jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="任务不存在或已结束")
    return {"ok": True, "message": "已请求取消，当前文件处理完成后停止"}


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
                item["mode"],
                item["status"],
                f"{item['confidence']:.2f}",
                item["source_name"],
                item["title"],
                item["artist"] or "",
                item["album"] or "",
                item["detail"],
            ]
        )
    content = "\ufeff" + buffer.getvalue()  # Excel-friendly UTF-8 BOM
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="musicpilot-{job_id}.csv"'},
    )
