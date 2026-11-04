from __future__ import annotations

from typing import Protocol

from musicpilot.domain.models import TrackMetadata


class MetadataProvider(Protocol):
    name: str

    async def search(
        self,
        *,
        title: str,
        artist: str | None = None,
        album: str | None = None,
        limit: int = 5,
    ) -> list[TrackMetadata]: ...


def candidate_from_dict(payload: dict[str, object]) -> TrackMetadata:
    def text(key: str) -> str | None:
        value = payload.get(key)
        return str(value) if value not in (None, "") else None

    def number(key: str) -> int | None:
        value = payload.get(key)
        try:
            return int(value) if value is not None else None  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None

    return TrackMetadata(
        title=text("title") or "",
        artist=text("artist"),
        album=text("album"),
        album_artist=text("album_artist"),
        year=number("year"),
        track_number=number("track_number"),
        disc_number=number("disc_number"),
        lyrics=text("lyrics"),
        cover_url=text("cover_url"),
        source=text("source") or "unknown",
        external_id=text("external_id") or "",
    )
