from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class JobCreate(BaseModel):
    kind: Literal["preview", "scrape"] = "preview"
    dry_run: bool | None = None
    paths: list[str] = Field(default_factory=list, max_length=500)
    organize: bool = True
    skip_low_confidence: bool = False


class FilePathPayload(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


class CapabilityProbe(BaseModel):
    probe: bool = False


class SettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
