from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChoiceOverride(BaseModel):
    """A metadata decision made by hand on the preview page."""

    title: str = ""
    artist: str | None = None
    album: str | None = None
    year: int | None = None
    track_number: int | None = None
    cover_url: str | None = Field(default=None, max_length=2000)
    source: str = "manual"
    external_id: str = ""


class JobCreate(BaseModel):
    kind: Literal["preview", "scrape"] = "preview"
    dry_run: bool | None = None
    paths: list[str] = Field(default_factory=list, max_length=500)
    organize: bool = True
    skip_low_confidence: bool = False
    # {source path: chosen metadata}; only honoured for a real (non dry-run) job.
    choices: dict[str, ChoiceOverride] = Field(default_factory=dict)


class FilePathPayload(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


class CapabilityProbe(BaseModel):
    probe: bool = False


class SettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
