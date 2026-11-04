from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from musicpilot import __version__
from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import CapabilityProbe
from musicpilot.domain.transfer import (
    describe_modes,
    device_of,
    inspect_modes,
    mode_label,
)

public_router = APIRouter(tags=["meta"])
router = APIRouter(tags=["meta"])


@public_router.get("/api/health")
def health() -> dict[str, object]:
    return {"status": "ok", "version": __version__, "time": time.time()}


@router.get("/about")
def about(context: AppContext = Depends(get_context)) -> dict[str, object]:
    return {
        "name": context.base.app_name,
        "version": __version__,
        "description": "只做音乐刮削与整理的自托管工具",
        "started_at": context.started_at,
        "auth_enabled": context.auth_enabled,
        "database": str(context.base.database_path),
    }


@router.get("/transfer-modes")
def transfer_modes() -> dict[str, object]:
    return {
        "modes": describe_modes(),
        "ordering_note": (
            "标签在转移之前写入源文件。硬链接与软链接模式下目标与源共享同一份数据，"
            "因此源文件的标签也会被补全，后续任一边被修改都会影响另一边。"
        ),
    }


@router.get("/system/capabilities")
def capabilities(
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    runtime = context.settings.get()
    source, library = runtime.source_root, runtime.library_root
    return {
        "source_dir": str(source),
        "library_dir": str(library),
        "source_exists": source.is_dir(),
        "library_exists": library.is_dir(),
        "same_device": (
            None
            if device_of(source) is None or device_of(library) is None
            else device_of(source) == device_of(library)
        ),
        "modes": [item.as_dict() for item in inspect_modes(source, library)],
    }


@router.post("/system/capabilities")
def probe_capabilities(
    payload: CapabilityProbe,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    runtime = context.settings.get()
    source, library = runtime.source_root, runtime.library_root
    return {
        "probed": payload.probe,
        "modes": [
            item.as_dict()
            for item in inspect_modes(source, library, probe=payload.probe)
        ],
    }


@router.get("/dashboard")
def dashboard(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    runtime = context.settings.get()
    source, library = runtime.source_root, runtime.library_root
    source_device, library_device = device_of(source), device_of(library)
    return {
        "settings": runtime.model_dump(mode="json"),
        "source_dir": str(source),
        "library_dir": str(library),
        "source_exists": source.is_dir(),
        "library_exists": library.is_dir(),
        "same_device": (
            None
            if source_device is None or library_device is None
            else source_device == library_device
        ),
        "capabilities": [item.as_dict() for item in inspect_modes(source, library)],
        "jobs": context.jobs.list(6),
        "recent_done": [
            {**row, "mode_label": mode_label(str(row.get("mode") or ""))}
            for row in context.store.recent_done(8)
        ],
        "version": __version__,
    }
