"""酷狗音乐：搜索走 song_search_v2，封面走 album/info，歌词走 krcs 的 search + download。

旧的 ``mobilecdn.kugou.com/api/v3/search/song`` 现在稳定返回空 body（接口已失效），
换成网页版在用的 ``songsearch.kugou.com/song_search_v2``：``data.lists`` 里每首带
``FileHash`` / ``SingerName`` / ``AlbumName``，FileHash 就是取词用的 id。

封面：``song_search_v2`` 的 ``AlbumImage`` 常年是空串，只有 ``Image``（歌手的头像图，
按 ``{size}`` 占位）——拿歌手头像当专辑封面等于骗人。所以这里用每首自带的 ``AlbumID``
去 ``mobiles.kugou.com/api/v3/album/info`` 取真正的专辑封面（同一套 ``{size}`` 模板），
搜索阶段并发查、按 AlbumID 去重，查不到就留空，绝不回落到歌手头像。

歌词两步走：``krcs.kugou.com/search?hash=<FileHash>`` 拿 candidates（id + accesskey），
再 ``krcs.kugou.com/download?fmt=lrc`` 拿正文。download 在 ``lyrics.kugou.com`` 上会挂住不返回，
所以两个请求都只打 ``krcs.kugou.com``。``fmt=lrc`` 回的是 base64 包裹的 LRC（不是 krc 格式），
解出来直接用；万一回了 ``krc1|"..."|`` 老包裹，先剥壳再当 LRC。
"""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import replace
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

_SEARCH_URL = "https://songsearch.kugou.com/song_search_v2"
_ALBUM_URL = "https://mobiles.kugou.com/api/v3/album/info"
_LYRIC_SEARCH_URL = "https://krcs.kugou.com/search"
_LYRIC_DOWNLOAD_URL = "https://krcs.kugou.com/download"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Referer": "https://www.kugou.com/",
}
_KRC_WRAPPED = re.compile(r'krc1\|"(?P<body>.*)"\|\s*$', re.S)
_HTML_TAG = re.compile(r"<[^>]+>")
_BASE64 = re.compile(r"^[A-Za-z0-9+/=\s]+$")
# 封面取 480px：够 72px 缩略图与编辑器大图，又不至于一次拉几百 KB
_COVER_SIZE = "480"


def _strip_krc(text: str) -> str:
    match = _KRC_WRAPPED.search(text.strip().replace("\n", ""))
    body = match.group("body") if match else text
    body = body.replace('\\"', '"').replace("\\r", "\n").replace("\\n", "\n")
    # krc 的逐字时间标签 [1234,567] 对播放器没意义，换成行的 -> LRC 行首时间
    body = re.sub(r"\[\d+,\d+\]", "", body)
    return body


def _plain(text: Any) -> str:
    return _HTML_TAG.sub("", str(text or "")).replace("&nbsp;", " ").strip()


def _cover(url: Any, size: str = _COVER_SIZE) -> str | None:
    """酷狗封面是 ``.../{size}/...jpg`` 模板，按需换尺寸；不带模板的原样返回。"""
    text = str(url or "").strip()
    if not text:
        return None
    if text.startswith("//"):
        text = f"https:{text}"
    return text.replace("{size}", size)


class KugouProvider:
    name = "kugou"
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
        try:
            response = await self._client.get(
                _SEARCH_URL,
                params={
                    "keyword": keyword,
                    "page": 1,
                    "pagesize": max(1, min(limit * 2, 20)),
                    "userid": -1,
                    "clientver": 2000,
                    "platform": "WebFilter",
                    "filter": 2,
                    "iscorrection": 1,
                    "privilege_filter": 0,
                },
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        songs = data.get("lists", []) if isinstance(data, dict) else []
        candidates: list[TrackMetadata] = []
        album_ids: list[str] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = _plain(song.get("SongName"))
            hash_id = song.get("FileHash") or song.get("EMixSongID")
            if not name or not hash_id:
                continue
            candidates.append(
                TrackMetadata(
                    title=name,
                    artist=join_names(_plain(song.get("SingerName")).split("、")),
                    album=_plain(song.get("AlbumName")) or None,
                    source=self.name,
                    external_id=str(hash_id),
                )
            )
            album_ids.append(str(song.get("AlbumID") or ""))
            if len(candidates) >= limit:
                break
        covers = await self._album_covers(album_ids)
        return [
            replace(candidate, cover_url=covers.get(album_id))
            for candidate, album_id in zip(candidates, album_ids, strict=True)
        ]

    async def _album_covers(self, album_ids: list[str]) -> dict[str, str | None]:
        """按 AlbumID 并发取专辑封面；同一个专辑只查一次，失败就当没有。"""
        wanted = [album_id for album_id in dict.fromkeys(album_ids) if album_id.isdigit()]
        if not wanted:
            return {}

        async def one(album_id: str) -> tuple[str, str | None]:
            try:
                response = await self._client.get(
                    _ALBUM_URL,
                    params={"albumid": album_id},
                    headers=_HEADERS,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:  # noqa: BLE001 - 没封面不该拖垮搜索
                return album_id, None
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                return album_id, None
            for key in ("imgurl", "imgurl9", "imgurl2", "banner"):
                cover = _cover(data.get(key))
                if cover:
                    return album_id, cover
            return album_id, None

        pairs = await asyncio.gather(*(one(album_id) for album_id in wanted))
        return dict(pairs)

    async def lyrics(self, song_id: str, *, album: str | None = None) -> LyricsResult:
        try:
            found = await self._client.get(
                _LYRIC_SEARCH_URL,
                params={
                    "ver": 1,
                    "man": "yes",
                    "client": "pc",
                    "keyword": "",
                    "duration": "",
                    "hash": song_id,
                },
                headers=_HEADERS,
            )
            found.raise_for_status()
            payload = found.json()
        except Exception:  # noqa: BLE001
            return LyricsResult(reason=REASON_NO_RESPONSE)
        candidates = payload.get("candidates") if isinstance(payload, dict) else None
        if not isinstance(candidates, list) or not candidates:
            return LyricsResult(reason=REASON_NO_LYRICS)
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        lyric_id, accesskey = first.get("id"), first.get("accesskey")
        if not lyric_id or not accesskey:
            return LyricsResult(reason=REASON_NO_LYRICS)
        try:
            download = await self._client.get(
                _LYRIC_DOWNLOAD_URL,
                params={
                    "ver": 1,
                    "client": "pc",
                    "id": lyric_id,
                    "accesskey": accesskey,
                    "fmt": "lrc",
                    "charset": "utf8",
                },
                headers=_HEADERS,
            )
            download.raise_for_status()
            body = download.json()
        except Exception:  # noqa: BLE001
            return LyricsResult(reason=REASON_NO_RESPONSE)
        content = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str) or not content.strip():
            return LyricsResult(reason=REASON_NO_LYRICS)
        text = content.strip()
        # fmt=lrc 回的是 base64 包裹的 LRC（老包裹 ``krc1|"..."|`` 也走这条路）；先解再剥壳。
        # 已经是明文 LRC 的（以 ``[ti:`` 这类标签开头）就别再动它。
        if not text.startswith("[") and not text.startswith("krc1|") and _BASE64.match(text):
            try:
                text = base64.b64decode(text, validate=False).decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return LyricsResult(reason=REASON_NO_RESPONSE)
        text = clean_lyrics(_strip_krc(text))
        return LyricsResult(text=text) if text else LyricsResult(reason=REASON_NO_LYRICS)
