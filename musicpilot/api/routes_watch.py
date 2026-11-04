"""自动刮削的开关与状态：轮询源目录，新文件出现即排队刮削。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import WatchPatch
from musicpilot.services.settings_service import SettingsError

router = APIRouter(prefix="/watch", tags=["watch"])


def _payload(context: AppContext) -> dict[str, Any]:
    runtime = context.settings.get()
    return {
        **context.watcher.status(),
        "interval": runtime.watch_interval,
        "settle": runtime.watch_settle,
        "source_dir": runtime.source_dir,
    }


@router.get("")
def read_watch(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    return _payload(context)


@router.put("")
def update_watch(
    patch: WatchPatch,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    fields = {
        "enabled": "watch_enabled",
        "interval": "watch_interval",
        "settle": "watch_settle",
    }
    values = {
        fields[key]: value
        for key, value in patch.model_dump().items()
        if value is not None and key in fields
    }
    if not values:
        return _payload(context)
    try:
        runtime = context.settings.update(values)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    context.watcher.applied(runtime)
    return _payload(context)


@router.post("/reset")
def reset_watch(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    """Forget the seen-file list: the next scan treats existing files as new again."""
    context.watcher.reset()
    return _payload(context)
