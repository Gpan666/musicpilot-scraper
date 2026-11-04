from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import SettingsPatch
from musicpilot.services.settings_service import SettingsError

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def read_settings(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    return context.settings.describe()


@router.put("")
def update_settings(
    patch: SettingsPatch,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    try:
        context.settings.update(patch.values)
    except SettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return context.settings.describe()
