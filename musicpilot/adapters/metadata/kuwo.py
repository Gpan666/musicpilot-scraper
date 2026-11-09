"""酷我音乐：搜索走 search.kuwo.cn/r.s，歌词走 m.kuwo.cn 的 songinfoandlrc。

``www.kuwo.cn/api/www/...`` 那两个接口现在一律回 ``The request is illegal!``（要浏览器端
算出来的 csrf/cookie），所以两边都换成不需要签名的老接口：

- 搜索：``search.kuwo.cn/r.s``（``mobi=1`` 才带专辑名与专辑封面，``pcjson=1`` 回标准 JSON），
  每首的 ``DC_TARGETID`` 就是取词用的 id。
- 歌词：``m.kuwo.cn/newh5/singles/songinfoandlrc`` 回 ``lrclist``（``time`` 是秒、``lineLyric`` 是一行词），
  拼回带 ``[mm:ss.xx]`` 的 LRC。这个接口偶发回 ``音乐查询失败``（301），重试一次再下结论。
"""

from __future__ import annotations

import ast
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

_SEARCH_URL = "https://search.kuwo.cn/r.s"
_LYRIC_URL = "https://m.kuwo.cn/newh5/singles/songinfoandlrc"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148"
    ),
    "Referer": "https://m.kuwo.cn/",
}
_HTML_TAG = re.compile(r"<[^>]+>")
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")
_ARTIST_SPLIT = re.compile(r"\\u0026|&|、|,")


def _plain(text: Any) -> str:
    """酷我给的是 ``&nbsp;`` 加 ``\\u0026`` 这类转义 HTML，先还原再当纯文本。"""
    raw = str(text or "")
    raw = _ESCAPED.sub(lambda m: chr(int(m.group(1), 16)), raw)
    return _HTML_TAG.sub("", raw).replace("&nbsp;", " ").strip()


def _decode(text: str) -> dict[str, Any] | None:
    """接口回的是 Python 字面量写法的 JSON（单引号），标准 json 解不动时退回 ast。"""
    body = text.strip()
    if not body:
        return None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(body)
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, dict) else None


_COVER_PREFIX = re.compile(r"^(\d{2,4})/")
# 列表缩略图与编辑器大图都够用的尺寸，一张百来 KB
_COVER_SIZE = 500


def _cover(path: Any) -> str | None:
    """``120/s3s94/93/211513640.jpg`` → 500px 的封面地址。

    酷我给的是相对路径，前缀那个数字就是尺寸；不换的话下载回来只有 120×120。
    """
    text = str(path or "").strip()
    if not text:
        return None
    text = _COVER_PREFIX.sub(f"{_COVER_SIZE}/", text, count=1)
    return f"https://img1.kuwo.cn/star/albumcover/{text}"


def _stamp(seconds: Any) -> str | None:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return None
    minutes = int(value // 60)
    rest = value - minutes * 60
    return f"[{minutes:02d}:{rest:05.2f}]"


def _lrc_from_rows(rows: list[Any]) -> str | None:
    """``lrclist`` → 带时间轴的 LRC；没有时间信息的行原样留在原位，不编造时间戳。"""
    lines: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("lineLyric") or "").rstrip()
        if not text.strip():
            continue
        stamp = _stamp(row.get("time"))
        lines.append(f"{stamp}{text}" if stamp else text)
    return clean_lyrics("\n".join(lines))


class KuwoProvider:
    name = "kuwo"
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
                    "all": keyword,
                    "ft": "music",
                    "client": "kt",
                    "cluster": 0,
                    "strategy": 2012,
                    "encoding": "utf8",
                    "rformat": "json",
                    "pcjson": 1,
                    "mobi": 1,
                    "vipver": 1,
                    "pn": 0,
                    "rn": max(1, min(limit * 2, 20)),
                },
                headers=_HEADERS,
            )
            response.raise_for_status()
            payload = _decode(response.text)
        except Exception:  # noqa: BLE001
            return []
        songs = payload.get("abslist") if isinstance(payload, dict) else None
        candidates: list[TrackMetadata] = []
        for song in songs if isinstance(songs, list) else []:
            if not isinstance(song, dict):
                continue
            name = _plain(song.get("SONGNAME") or song.get("NAME") or song.get("name"))
            rid = song.get("DC_TARGETID") or song.get("MUSICRID") or song.get("rid")
            if not name or rid in (None, ""):
                continue
            artist_name = _plain(song.get("ARTIST") or song.get("FARTIST")) or None
            album_name = _plain(song.get("ALBUM"))
            cover_short = _plain(song.get("web_albumpic_short"))
            release = _plain(song.get("web_timingonline"))
            year = int(release[:4]) if release[:4].isdigit() else None
            artists = _ARTIST_SPLIT.split(_plain(song.get("FARTIST") or song.get("ARTIST")))
            candidates.append(
                TrackMetadata(
                    title=name,
                    artist=join_names(artists) or artist_name,
                    album=album_name or None,
                    # 酷我字段里没有单独的专辑艺术家位置，合辑靠歌手 / 专辑名匹配
                    album_artist=artist_name,
                    year=year,
                    # web_albumpic_short 带 ``120/`` 前缀：那是缩略图尺寸，换成 500 才是大图
                    cover_url=_cover(cover_short),
                    source=self.name,
                    external_id=str(rid).removeprefix("MUSIC_"),
                )
            )
            if len(candidates) >= limit:
                break
        return candidates

    async def lyrics(self, song_id: str, *, album: str | None = None) -> LyricsResult:
        payload: dict[str, Any] | None = None
        for _ in range(2):  # 偶发 301「音乐查询失败」，重试一次再说
            try:
                response = await self._client.get(
                    _LYRIC_URL,
                    params={"musicId": song_id, "httpsStatus": 1},
                    headers=_HEADERS,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:  # noqa: BLE001
                payload = None
                continue
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                break
        if not isinstance(payload, dict):
            return LyricsResult(reason=REASON_NO_RESPONSE)
        data = payload.get("data")
        if not isinstance(data, dict):
            # 接口正常应答但说「音乐查询失败」（多为版权没给词）：原话说清楚，
            # 不能谎称「该歌曲没有歌词」——这首歌有没有词我们并不知道。
            message = str(payload.get("msg") or "").strip()
            return LyricsResult(reason=f"接口返回「{message}」" if message else REASON_NO_RESPONSE)
        text = _lrc_from_rows(data.get("lrclist") or [])
        return LyricsResult(text=text) if text else LyricsResult(reason=REASON_NO_LYRICS)
