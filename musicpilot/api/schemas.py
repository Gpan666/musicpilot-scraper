from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChoiceOverride(BaseModel):
    """A metadata decision made by hand on the preview page."""

    title: str = ""
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
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


class WatchPatch(BaseModel):
    """自动刮削开关；只提交想改的字段。"""

    enabled: bool | None = None
    interval: float | None = Field(default=None, ge=5, le=3600)
    settle: float | None = Field(default=None, ge=0, le=3600)


class SettingsPatch(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class LibrarySearchRequest(BaseModel):
    """手动刮削：在指定平台（或全部已启用平台）搜索。"""

    path: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="", max_length=40)
    title: str | None = Field(default=None, max_length=300)
    artist: str | None = Field(default=None, max_length=300)
    album: str | None = Field(default=None, max_length=300)
    limit: int = Field(default=8, ge=1, le=20)


class LibraryScrapeApply(BaseModel):
    """手动刮削：把选中的候选写进标签（只补空字段）。"""

    path: str = Field(min_length=1, max_length=2000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 是否连同歌词一起补；fetch_lyrics 才真的去音源请求歌词
    with_lyrics: bool = True
    fetch_lyrics: bool = False
    lyrics_only: bool = False
    dry_run: bool = False


class LibraryLyricsFetch(BaseModel):
    """从音源按候选 id 取词：只读，不写任何文件。"""

    source: str = Field(min_length=1, max_length=40)
    external_id: str = Field(min_length=1, max_length=200)
    album: str | None = Field(default=None, max_length=300)
    title: str | None = Field(default=None, max_length=300)


class LibraryLyricsApply(BaseModel):
    path: str = Field(min_length=1, max_length=2000)
    lyrics: str = Field(default="", max_length=200_000)
    dry_run: bool = False


class LibraryFillRequest(BaseModel):
    """自动扫描查漏补缺：补全元数据，绝不改动文件位置或链接关系。"""

    sources: list[str] = Field(default_factory=list, max_length=10)
    paths: list[str] = Field(default_factory=list, max_length=2000)
    with_lyrics: bool = True
    dry_run: bool = False
    limit: int | None = Field(default=None, ge=1, le=2000)
