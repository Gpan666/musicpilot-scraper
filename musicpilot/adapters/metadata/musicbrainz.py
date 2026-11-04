"""MusicBrainz recording search (respects the 1 request/second rate limit)."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from musicpilot.domain.models import TrackMetadata

_SEARCH_URL = "https://musicbrainz.org/ws/2/recording"
_rate_lock = asyncio.Lock()
_last_request = 0.0


def _phrase(value: str) -> str:
    return value.strip().replace("\\", "\\\\").replace('"', '\\"')


def _query(title: str, artist: str | None, album: str | None) -> str:
    parts = []
    if title.strip():
        parts.append(f'recording:"{_phrase(title)}"')
    if artist and artist.strip():
        parts.append(f'artist:"{_phrase(artist)}"')
    if album and album.strip():
        parts.append(f'release:"{_phrase(album)}"')
    return " AND ".join(parts) if parts else _phrase(title)


def _year(value: Any) -> int | None:
    text = str(value or "")[:4]
    try:
        return int(text) if text.isdigit() else None
    except ValueError:
        return None


class MusicBrainzProvider:
    name = "musicbrainz"

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def search(
        self,
        *,
        title: str,
        artist: str | None = None,
        album: str | None = None,
        limit: int = 5,
    ) -> list[TrackMetadata]:
        global _last_request  # noqa: PLW0603 - module-level rate limiter
        async with _rate_lock:
            wait = 1.1 - (time.monotonic() - _last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            _last_request = time.monotonic()
        try:
            response = await self._client.get(
                _SEARCH_URL,
                params={
                    "query": _query(title, artist, album),
                    "fmt": "json",
                    "limit": max(1, min(limit * 3, 25)),
                    "inc": "artist-credits+releases",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - offline / rate limited / malformed response
            return []

        recordings = payload.get("recordings", []) if isinstance(payload, dict) else []
        results: list[TrackMetadata] = []
        seen: set[tuple[str, str, str]] = set()
        for recording in recordings if isinstance(recordings, list) else []:
            if not isinstance(recording, dict):
                continue
            track_title = str(recording.get("title") or title)
            credits = recording.get("artist-credit") or []
            artist_name = None
            if isinstance(credits, list) and credits and isinstance(credits[0], dict):
                artist_name = credits[0].get("name")
            releases = recording.get("releases") or [{}]
            for release in (releases if isinstance(releases, list) else [])[:2]:
                if not isinstance(release, dict):
                    continue
                album_name = release.get("title")
                release_id = release.get("id")
                key = (
                    track_title.casefold(),
                    str(artist_name or "").casefold(),
                    str(album_name or "").casefold(),
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    TrackMetadata(
                        title=track_title,
                        artist=str(artist_name) if artist_name else None,
                        album=str(album_name) if album_name else None,
                        year=_year(release.get("date")),
                        cover_url=(
                            f"https://coverartarchive.org/release/{release_id}/front-250"
                            if release_id
                            else None
                        ),
                        source=self.name,
                        external_id=str(recording.get("id") or ""),
                    )
                )
                if len(results) >= limit:
                    return results
        return results
