"""QQ 音乐：搜索走 musicu.fcg 的 mobile 方法，歌词走 fcg_query_lyric_new。

老的 ``c.y.qq.com/soso/fcgi-bin/client_search_cgi`` 已经返回 404（接口下线），
所以搜索换成网页版现在仍在用的 ``u.y.qq.com/cgi-bin/musicu.fcg``：
``DoSearchForQQMusicMobile`` 回的是标准 JSON，单曲在 ``item_song`` 里，mid 就是取词用的 id。
歌词接口 ``fcg_query_lyric_new.fcg`` 仍可用，给 mid 就回 LRC 文本（``nobase64=1`` 免解码）。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import (
    REASON_NO_LYRICS,
    REASON_NO_RESPONSE,
    LyricsResult,
    clean_lyrics,
    join_names,
)
from musicpilot.domain.models import TrackMetadata

_SEARCH_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
_HEADERS = {
    "Referer": "https://y.qq.com/",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
}
_WRAPPED = re.compile(r"^[^(]*\((.*)\)[;\s]*$", re.S)
# 网页播放器自己用的 comm 参数，缺了会被判成非法请求
_COMM = {"ct": 11, "cv": "1003006", "v": "1003006", "uin": "0", "format": "json"}


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
    supports_lyrics = True
    lyrics_note = ""
    search_note = ""

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
        payload = {
            "comm": _COMM,
            "req_1": {
                "module": "music.search.SearchCgiService",
                "method": "DoSearchForQQMusicMobile",
                "param": {
                    "search_type": 0,
                    "query": keyword,
                    "page_num": 1,
                    "num_per_page": max(1, min(limit * 2, 20)),
                },
            },
        }
        try:
            response = await self._client.get(
                _SEARCH_URL,
                params={"format": "json", "data": json.dumps(payload, ensure_ascii=False)},
                headers=_HEADERS,
            )
            response.raise_for_status()
            parsed = response.json()
        except Exception:  # noqa: BLE001 - 接口被墙 / 限流 / 改版都不该拖垮任务
            return []
        body = (
            parsed.get("req_1", {}).get("data", {}).get("body", {})
            if isinstance(parsed, dict)
            else {}
        )
        songs = body.get("item_song") if isinstance(body, dict) else []
        candidates: list[TrackMetadata] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = str(song.get("name") or song.get("title") or "").strip()
            song_mid = song.get("mid")
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
                    year=_year(album_info.get("time_public") or song.get("time_public")),
                    track_number=_int(song.get("index_album")),
                    cover_url=_cover(album_info.get("mid")),
                    source=self.name,
                    external_id=str(song_mid),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def lyrics(self, song_id: str, *, album: str | None = None) -> LyricsResult:
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
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = _unwrap(response.text)
        except Exception:  # noqa: BLE001
            return LyricsResult(reason=REASON_NO_RESPONSE)
        if not isinstance(payload, dict):
            return LyricsResult(reason=REASON_NO_RESPONSE)
        raw = str(payload.get("lyric") or "").replace("&#58;", ":").replace("&amp;", "&")
        text = clean_lyrics(raw)
        return LyricsResult(text=text) if text else LyricsResult(reason=REASON_NO_LYRICS)
