"""QQ 音乐：搜索走 client_search_cgi，歌词走 lyric 接口。

QQ 音乐没有公开 API，这里用的是网页版自己调的接口，返回的是 JSON 里嵌回调函数的
老式写法（``callback({...})``），所以先把外层函数名剥掉再解析。
``inCharset/outCharset=utf8`` 不能省，默认给的是 GBK。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import clean_lyrics, join_names
from musicpilot.domain.models import TrackMetadata

_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cgi"
_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
_HEADERS = {
    "Referer": "https://y.qq.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
_WRAPPED = re.compile(r"^[^(]*\((.*)\)[;\s]*$", re.S)


def _unwrap(text: str) -> Any:
    """``callback({...})`` / ``MusicJsonCallback(...)`` → 解析出的对象。"""
    body = text.strip()
    match = _WRAPPED.match(body)
    if match:
        body = match.group(1)
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _cover(album_mid: Any) -> str | None:
    if not album_mid:
        return None
    return f"https://y.gtimg.cn/music/photo_new/T002R500x500M000{album_mid}.jpg"


def _year(date_text: Any) -> int | None:
    head = str(date_text or "")[:4]
    return int(head) if head.isdigit() else None


class QQMusicProvider:
    name = "qq"

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
                    "w": keyword,
                    "format": "json",
                    "n": max(1, min(limit * 2, 20)),
                    "p": 1,
                    "cr": 1,
                    "new_json": 1,
                    "inCharset": "utf8",
                    "outCharset": "utf8",
                },
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = _unwrap(response.text)
        except Exception:  # noqa: BLE001 - 接口被墙 / 限流 / 改版都不该拖垮任务
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        songs = data.get("song", {}).get("list", []) if isinstance(data, dict) else []
        candidates: list[TrackMetadata] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = str(song.get("title") or song.get("songname") or "").strip()
            song_mid = song.get("mid") or song.get("songmid")
            if not name or not song_mid:
                continue
            singers = song.get("singer") or []
            singers = singers if isinstance(singers, list) else []
            artist_names = join_names(
                [item.get("name") or item.get("title") for item in singers if isinstance(item, dict)]
            )
            album_info = song.get("album") if isinstance(song.get("album"), dict) else {}
            candidates.append(
                TrackMetadata(
                    title=name,
                    artist=artist_names,
                    album=str(album_info.get("name") or album_info.get("title") or "") or None,
                    # QQ 搜索结果不带专辑艺术家，合辑靠专辑名匹配即可
                    year=_year(album_info.get("release_date") or song.get("time_public")),
                    track_number=_int(song.get("index_album")) or _int(song.get("track_number")),
                    cover_url=_cover(album_info.get("mid")),
                    source=self.name,
                    external_id=str(song_mid),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def lyrics(self, song_id: str, *, album: str | None = None) -> str | None:
        try:
            response = await self._client.get(
                _LYRIC_URL,
                params={
                    "songmid": song_id,
                    "format": "json",
                    "nobase64": 1,
                    "g_tk": 5381,
                    "inCharset": "utf8",
                    "outCharset": "utf8",
                },
                headers={**_HEADERS, "Host": "c.y.qq.com"},
            )
            response.raise_for_status()
            payload = _unwrap(response.text)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(payload, dict):
            return None
        return clean_lyrics(str(payload.get("lyric") or "").replace("&#58;", ":").replace("&amp;", "&"))
