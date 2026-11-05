"""酷狗音乐：搜索走 mobilecdn 的公开接口，歌词走 krc 接口。

两处都不一样：搜索是普通 JSON（``data.info``），歌词是 ``krc1|"内容"|`` 这种
带前缀和后缀的包裹格式，取中间那段再处理。
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from musicpilot.adapters.metadata.base import clean_lyrics, join_names
from musicpilot.domain.models import TrackMetadata

_SEARCH_URL = "https://mobilecdn.kugou.com/api/v3/search/song"
_LYRIC_URL = "https://krcs.kugou.com/search"
_LYRIC_DOWNLOAD_URL = "https://lyrics.kugou.com/download"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
}
_KRC_WRAPPED = re.compile(r'krc1\|"(?P<body>.*)"\|\s*$', re.S)


def _strip_krc(text: str) -> str:
    match = _KRC_WRAPPED.search(text.strip().replace("\n", ""))
    body = match.group("body") if match else text
    body = body.replace('\\"', '"').replace("\\r", "\n").replace("\\n", "\n")
    # krc 的逐字时间标签 [1234,567] 对播放器没意义，换成行的 -> LRC 行首时间
    body = re.sub(r"\[\d+,\d+\]", "", body)
    return body


class KugouProvider:
    name = "kugou"

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
                    "format": "json",
                    "keyword": keyword,
                    "page": 1,
                    "pagesize": max(1, min(limit * 2, 20)),
                    "showtype": 1,
                },
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        songs = data.get("info", []) if isinstance(data, dict) else []
        candidates: list[TrackMetadata] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = str(song.get("songname") or "").strip()
            # 带 <em> 高亮标签，搜索结果里常见，去掉再用
            name = re.sub(r"<[^>]+>", "", name).strip()
            hash_id = song.get("hash") or song.get("320hash") or song.get("sqhash")
            if not name or not hash_id:
                continue
            album_name = re.sub(r"<[^>]+>", "", str(song.get("album_name") or "")).strip()
            candidates.append(
                TrackMetadata(
                    title=name,
                    artist=join_names(str(song.get("singername") or "").split("、")),
                    album=album_name or None,
                    source=self.name,
                    external_id=str(hash_id),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def lyrics(self, song_id: str, *, album: str | None = None) -> str | None:
        try:
            found = await self._client.get(
                _LYRIC_URL,
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
            candidates = payload.get("candidates") if isinstance(payload, dict) else None
            if not isinstance(candidates, list) or not candidates:
                return None
            first = candidates[0] if isinstance(candidates[0], dict) else {}
            lyric_id, accesskey = first.get("id"), first.get("accesskey")
            if not lyric_id or not accesskey:
                return None
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
            return None
        content: Any = body.get("content") if isinstance(body, dict) else None
        if not isinstance(content, str):
            return None
        text = _strip_krc(content)
        # 接口偶尔直接给 base64，兜一层
        if not text.strip().startswith("[") and re.fullmatch(r"[A-Za-z0-9+/=\n]+", text.strip() or "x"):
            import base64

            try:
                text = base64.b64decode(text).decode("utf-8", "ignore")
            except Exception:  # noqa: BLE001
                return None
        return clean_lyrics(text)
