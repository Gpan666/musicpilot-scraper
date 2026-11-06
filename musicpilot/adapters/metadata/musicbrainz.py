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


def _track_number(value: Any) -> int | None:
    text = str(value or "").strip()
    try:
        return int(text) if text.isdigit() else None
    except ValueError:
        return None


class MusicBrainzProvider:
    name = "musicbrainz"
    # MusicBrainz 是元数据数据库，本身不存歌词正文；中文曲库覆盖也低。
    # 这里如实标记，不假装能取词，也不凭空造词。
    supports_lyrics = False
    lyrics_note = "MusicBrainz 不提供歌词正文"
    search_note = "仅适合英文 / 国际曲目，中文曲库覆盖很低"

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
                    "inc": "artist-credits+releases+recording-level-rels",
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
                # 专辑艺术家取自 release 的 artist-credit（合辑里和单曲歌手不同），
                # 之前只用 recording 的歌手，导致 {album_artist} 永远回落成歌手。
                album_artist = artist_name
                release_credits = release.get("artist-credit") or []
                if isinstance(release_credits, list) and release_credits:
                    credit = release_credits[0]
                    if isinstance(credit, dict) and credit.get("name"):
                        album_artist = credit.get("name")
                track_number = None
                media = release.get("media") or []
                if isinstance(media, list) and media and isinstance(media[0], dict):
                    tracks = media[0].get("track") or []
                    if isinstance(tracks, list) and tracks and isinstance(tracks[0], dict):
                        track_number = tracks[0].get("number") or tracks[0].get("position")
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
                        album_artist=str(album_artist) if album_artist else None,
                        year=_year(release.get("date")),
                        track_number=_track_number(track_number),
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
