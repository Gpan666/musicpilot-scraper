"""NetEase Cloud Music public search (Chinese catalogue, covers and lyrics)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from musicpilot.domain.models import TrackMetadata

_SEARCH_URL = "https://music.163.com/api/search/get/web"
_LYRIC_URL = "https://music.163.com/api/song/lyric"


def _year_from_ms(value: Any) -> int | None:
    try:
        return datetime.fromtimestamp(int(value) / 1000, UTC).year
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class NetEaseProvider:
    name = "netease"

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
        keyword = " ".join(part for part in (title, artist or "") if part.strip())
        try:
            response = await self._client.get(
                _SEARCH_URL,
                params={"s": keyword, "type": 1, "limit": max(1, min(limit * 2, 20)), "offset": 0},
                headers={"Referer": "https://music.163.com/"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - endpoint may be rate limited or blocked
            return []
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        songs = result.get("songs", []) if isinstance(result, dict) else []
        candidates: list[TrackMetadata] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = str(song.get("name") or "").strip()
            song_id = song.get("id")
            if not name or not song_id:
                continue
            artists = song.get("artists") or []
            artist_names = ", ".join(
                str(item.get("name") or "").strip()
                for item in artists
                if isinstance(item, dict) and item.get("name")
            )
            album_info = song.get("album") if isinstance(song.get("album"), dict) else {}
            # 网易云搜索结果里 album.artist 是专辑艺术家，和歌曲歌手（合唱、feat.）可能不同
            album_artists = album_info.get("artist") if isinstance(album_info, dict) else None
            album_artist = None
            if isinstance(album_artists, dict) and album_artists.get("name"):
                album_artist = str(album_artists.get("name")).strip()
            elif isinstance(album_artists, list):
                album_artist = ", ".join(
                    str(item.get("name") or "").strip()
                    for item in album_artists
                    if isinstance(item, dict) and item.get("name")
                ) or None
            candidates.append(
                TrackMetadata(
                    title=name,
                    artist=artist_names or None,
                    album=str(album_info.get("name") or "") or None,
                    album_artist=album_artist,
                    year=_year_from_ms(album_info.get("publishTime")),
                    cover_url=str(album_info.get("picUrl") or "") or None,
                    source=self.name,
                    external_id=str(song_id),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def lyrics(self, song_id: str) -> str | None:
        try:
            response = await self._client.get(
                _LYRIC_URL,
                params={"id": song_id, "lv": -1, "kv": -1, "tv": -1},
                headers={"Referer": "https://music.163.com/"},
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict):
            return None
        lrc = payload.get("lrc", {})
        translated = payload.get("tlyric", {})
        original = str(lrc.get("lyric") or "").strip() if isinstance(lrc, dict) else ""
        extra = str(translated.get("lyric") or "").strip() if isinstance(translated, dict) else ""
        combined = "\n".join(part for part in (original, extra) if part)
        return combined or None
