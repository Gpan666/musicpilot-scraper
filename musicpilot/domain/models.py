"""Plain data structures shared by the API, services and adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobKind(StrEnum):
    PREVIEW = "preview"
    SCRAPE = "scrape"
    AUTO = "auto"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ItemStatus(StrEnum):
    PLANNED = "planned"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class TrackKind(StrEnum):
    """曲目类型：单曲独占一个目录，专辑曲目一个目录多首。"""

    SINGLE = "single"
    ALBUM = "album"


# 单曲本来就没有专辑与专辑轨号，只要求标题与歌手（封面、歌词另算）。
SINGLE_REQUIRED_FIELDS = ("title", "artist")
# 专辑曲目维持原有六项：专辑艺术家也是「该有」的字段，默认模板按 {album_artist}
# 建目录，缺了它就得回落到歌手，专辑合辑会被拆散。
ALBUM_REQUIRED_FIELDS = ("title", "artist", "album", "album_artist", "year", "track_number")


@dataclass(frozen=True, slots=True)
class TrackMetadata:
    """The best-known metadata for a single track."""

    title: str = ""
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: int | None = None
    track_number: int | None = None
    disc_number: int | None = None
    lyrics: str | None = None
    cover_url: str | None = None
    source: str = "local"
    external_id: str = ""
    # 只有本地曲目会判定类型；在线候选留空，按「专辑曲目」处理（保守：宁可多补也不漏补）
    kind: str = ""
    kind_reason: str = ""

    @property
    def is_single(self) -> bool:
        return self.kind == TrackKind.SINGLE

    def required_fields(self) -> tuple[str, ...]:
        return SINGLE_REQUIRED_FIELDS if self.is_single else ALBUM_REQUIRED_FIELDS

    def missing_fields(self) -> tuple[str, ...]:
        return tuple(name for name in self.required_fields() if not getattr(self, name))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AudioFile:
    """A scanned source file with whatever tags it already carries."""

    path: str
    size: int
    modified_at: float
    extension: str
    duration: float | None = None
    bitrate: int | None = None
    local_metadata: TrackMetadata = field(default_factory=TrackMetadata)
    has_embedded_cover: bool = False
    read_error: str = ""

    @property
    def path_object(self) -> Path:
        return Path(self.path)

    @property
    def display_name(self) -> str:
        return Path(self.path).name

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["display_name"] = self.display_name
        return payload


@dataclass(frozen=True, slots=True)
class MetadataCandidate:
    metadata: TrackMetadata
    score: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"metadata": self.metadata.as_dict(), "score": round(self.score, 4)}


@dataclass(frozen=True, slots=True)
class PlanItem:
    """One row of a dry-run plan or of an executed job."""

    source: str
    target: str
    mode: str
    status: str
    confidence: float = 0.0
    source_name: str = "local"
    title: str = ""
    artist: str | None = None
    album: str | None = None
    album_artist: str | None = None
    year: int | None = None
    track_number: int | None = None
    detail: str = ""
    link_target: str | None = None
    local_title: str = ""
    local_artist: str | None = None
    local_album: str | None = None
    local_album_artist: str | None = None
    local_year: int | None = None
    local_track_number: int | None = None
    local_lyrics: str | None = None
    has_local_lyrics: bool = False
    has_lyrics: bool = False
    cover_url: str | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    id: str
    kind: str
    status: str
    total: int
    done: int
    failed: int
    skipped: int
    created_at: float
    processed: int = 0
    finished_at: float | None = None
    message: str = ""
    dry_run: bool = False
    current_file: str = ""
    title: str = ""
    auto: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
