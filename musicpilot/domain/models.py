"""Plain data structures shared by the API, services and adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class JobKind(StrEnum):
    PREVIEW = "preview"
    SCRAPE = "scrape"


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

    def missing_fields(self) -> tuple[str, ...]:
        missing = []
        for name in ("title", "artist", "album", "year", "track_number"):
            if not getattr(self, name):
                missing.append(name)
        return tuple(missing)

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
    year: int | None = None
    track_number: int | None = None
    detail: str = ""
    link_target: str | None = None
    local_title: str = ""
    local_artist: str | None = None
    local_album: str | None = None
    local_year: int | None = None
    local_track_number: int | None = None
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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
