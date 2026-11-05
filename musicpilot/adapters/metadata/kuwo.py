"""酷我音乐：搜索 ``search/music/search``，歌词 ``search/music/lyric``。

酷我的接口最朴素：搜索直接回 ``abslist``，歌词回 ``data.lyric``（LRC 文本）。
缺点是字段里没有专辑艺术家的明确位置，``artist`` 是歌曲歌手，专辑艺术家只能沿用歌手。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import clean_lyrics
from musicpilot.domain.models import TrackMetadata

_SEARCH_URL = "https://www.kuwo.cn/api/www/search/searchMusicBykeyWord"
_LYRIC_URL = "https://www.kuwo.cn/api/www/search/searchLyricsBySongId"
_HEADERS = {
    "Referer": "https://www.kuwo.cn/",
    "csrf": "kuwo",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
_HTML_TAG = re.compile(r"<[^>]+>")


def _rid(item: dict[str, Any]) -> str | None:
    for key in ("rid", "musicrid", "id"):
        value = item.get(key)
        if value not in (None, ""):
            return str(value).removeprefix("MUSIC_")
    return None


def _year(value: Any) -> int | None:
    head = str(value or "")[:4]
    return int(head) if head.isdigit() else None


class KuwoProvider:
    name = "kuwo"

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
        keyword = " ".join(part for part in (title, artist or "") if part and part.strip())
        try:
            response = await self._client.get(
                _SEARCH_URL,
                params={
                    "key": keyword,
                    "pn": 1,
                    "rn": max(1, min(limit * 2, 20)),
                    "httpsStatus": 1,
                },
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        songs = data.get("list", []) if isinstance(data, dict) else []
        candidates: list[TrackMetadata] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = _HTML_TAG.sub("", str(song.get("name") or song.get("songName") or "")).strip()
            rid = _rid(song)
            if not name or not rid:
                continue
            artist_name = str(song.get("artist") or "").strip() or None
            album_name = str(song.get("album") or "").strip()
            cover = str(song.get("pic") or song.get("web_albumpic_short") or "").strip()
            track_number = song.get("track")
            try:
                track_number = int(track_number) if track_number not in (None, "") else None
            except (TypeError, ValueError):
                track_number = None
            candidates.append(
                TrackMetadata(
                    title=name,
                    artist=artist_name,
                    album=album_name or None,
                    album_artist=artist_name,
                    year=_year(song.get("releaseDate")),
                    track_number=track_number,
                    cover_url=cover or None,
                    source=self.name,
                    external_id=rid,
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def lyrics(self, song_id: str, *, album: str | None = None) -> str | None:
        try:
            response = await self._client.get(
                _LYRIC_URL,
                params={"musicId": song_id, "httpsStatus": 1},
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        text = data.get("lyric") if isinstance(data, dict) else None
        return clean_lyrics(text if isinstance(text, str) else None)
