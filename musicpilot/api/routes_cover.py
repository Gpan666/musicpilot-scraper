"""Cover art proxy so the browser never talks to third-party hosts directly."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from musicpilot.adapters.metadata.service import cover_embedding
from musicpilot.adapters.tags import fetch_cover
from musicpilot.api.context import AppContext, get_context

router = APIRouter(tags=["cover"])

# ``fetch_cover`` is only ever pointed at artwork hosts by the providers; the
# proxy keeps an explicit list so it cannot be used as an open relay.
_ALLOWED_HOSTS = frozenset(
    {
        "music.163.com",  # 网易云
        "musicbrainz.org",  # MusicBrainz
        "y.gtimg.cn",  # QQ 音乐
        "www.kuwo.cn",  # 酷我
    }
)
_ALLOWED_SUFFIXES = (
    ".music.126.net",
    ".archive.org",
    ".gtimg.cn",  # QQ 音乐图片 CDN
    ".kugou.com",  # 酷狗
    ".kuwo.cn",  # 酷我
    ".kwcdn.com",  # 酷我图片 CDN
)
_CACHE = {"Cache-Control": "private, max-age=86400"}


def _allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return host in _ALLOWED_HOSTS or host.endswith(_ALLOWED_SUFFIXES)


def _resolve_in_source(path: str, context: AppContext) -> tuple[Path | None, str]:
    root = context.settings.get().source_root.resolve()
    try:
        target = Path(path).resolve()
    except OSError:
        return None, "路径无效"
    if not (target.is_relative_to(root) and target.is_file()):
        return None, "文件不在源目录内"
    return target, ""


@router.get("/cover")
async def cover(
    url: str = Query(default="", max_length=2000),
    path: str = Query(default="", max_length=2000),
    context: AppContext = Depends(get_context),
) -> Response:
    """Remote artwork by URL, or the cover embedded in a source file."""
    if path:
        target, message = await asyncio.to_thread(_resolve_in_source, path, context)
        if target is None:
            raise HTTPException(status_code=400, detail=message)
        payload = await cover_embedding(target)
        if payload is None:
            raise HTTPException(status_code=404, detail="该文件没有内嵌封面")
        return Response(content=payload[0], media_type=payload[1], headers=_CACHE)

    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 参数")
    if not _allowed(url) or context.metadata.client is None:
        raise HTTPException(status_code=403, detail="不支持的封面来源")
    fetched = await fetch_cover(url, client=context.metadata.client)
    if fetched is None:
        raise HTTPException(status_code=404, detail="无法获取封面")
    return Response(content=fetched[0], media_type=fetched[1], headers=_CACHE)
