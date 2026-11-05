"""音乐库路由：列表、详情、封面、手动刮削搜索、自动扫描查漏补缺。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from musicpilot.adapters.metadata.base import candidate_from_dict, clean_lyrics
from musicpilot.adapters.metadata.service import SOURCE_LABELS
from musicpilot.adapters.tags import write_missing_tags
from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import (
    LibraryFillRequest,
    LibraryLyricsApply,
    LibraryScrapeApply,
    LibrarySearchRequest,
)
from musicpilot.domain.library import FIELD_LABELS, match_score, missing_fields
from musicpilot.domain.models import TrackMetadata
from musicpilot.domain.transfer import mode_label
from musicpilot.services.library import LibraryTrack

router = APIRouter(prefix="/library", tags=["library"])

# 手动刮削可以选哪些来源：本地标签只做参考，不作为「下载来源」。
SCRAPE_SOURCES = ("musicbrainz", "netease", "qq", "kugou", "kuwo")


def _sources_payload(context: AppContext, runtime: Any) -> list[dict[str, Any]]:
    enabled = [name for name in runtime.metadata_sources if name in SCRAPE_SOURCES]
    return [
        {
            "id": name,
            "label": SOURCE_LABELS.get(name, name),
            "enabled": name in enabled,
        }
        for name in SCRAPE_SOURCES
    ]


def _track_or_404(context: AppContext, path: str) -> LibraryTrack:
    runtime = context.settings.get()
    try:
        return context.library.find(path, runtime)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("")
async def list_library(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    runtime = context.settings.get()
    payload = await asyncio.to_thread(context.library.list_tracks, runtime)
    payload["sources"] = _sources_payload(context, runtime)
    payload["write_tags"] = runtime.write_tags
    payload["transfer_modes"] = [
        {"mode": mode, "label": mode_label(mode)} for mode in ("copy", "move", "hardlink", "symlink")
    ]
    return payload


@router.get("/cover")
async def library_cover(
    path: str = Query(min_length=1, max_length=2000),
    context: AppContext = Depends(get_context),
) -> Response:
    from musicpilot.adapters.metadata.service import cover_embedding

    track = await asyncio.to_thread(_track_or_404, context, path)
    payload = await cover_embedding(Path(track.path))
    if payload is None:
        raise HTTPException(status_code=404, detail="该文件没有内嵌封面")
    return Response(
        content=payload[0],
        media_type=payload[1],
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/detail")
async def library_detail(
    path: str = Query(min_length=1, max_length=2000),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    runtime = context.settings.get()
    track = await asyncio.to_thread(_track_or_404, context, path)
    payload = track.detail()
    payload["sources"] = _sources_payload(context, runtime)
    payload["directory"] = str(Path(track.path).parent)
    return payload


async def _search_one(
    context: AppContext,
    runtime: Any,
    track: LibraryTrack,
    source: str,
    query: TrackMetadata,
    limit: int,
) -> list[dict[str, Any]]:
    provider = context.metadata.providers.get(source)
    if provider is None:
        return []
    try:
        found = await provider.search(
            title=query.title,
            artist=query.artist,
            album=query.album,
            limit=limit,
        )
    except Exception:  # noqa: BLE001 - 网络/接口异常按「没搜到」处理
        return []
    items = []
    for candidate in found:
        items.append(
            _candidate_payload(track, candidate, lyrics_fetcher=context.metadata)
        )
    return items


def _candidate_payload(
    track: LibraryTrack,
    candidate: TrackMetadata,
    *,
    lyrics_fetcher: Any | None = None,
) -> dict[str, Any]:
    query = track.metadata
    if not query.title.strip():
        from musicpilot.domain.matcher import parse_filename

        query = parse_filename(Path(track.path))
    meta = candidate.as_dict()
    meta["lyrics"] = clean_lyrics(candidate.lyrics)
    return {
        "score": match_score(query, candidate),
        "fills": [name for name in missing_fields(track.metadata, has_cover=track.has_cover)
                  if getattr(candidate, name, None)],
        "metadata": meta,
        "source_label": SOURCE_LABELS.get(candidate.source, candidate.source),
        "can_fetch_lyrics": bool(lyrics_fetcher),
    }


@router.post("/search")
async def library_search(
    payload: LibrarySearchRequest,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """手动刮削第一步：在指定平台搜索，结果带 1–10 的匹配度。"""
    runtime = context.settings.get()
    track = await asyncio.to_thread(_track_or_404, context, payload.path)

    source = (payload.source or "").strip().lower()
    if source and source not in SCRAPE_SOURCES:
        raise HTTPException(status_code=400, detail="不支持的来源平台")
    query = track.metadata
    title = (payload.title or "").strip()
    artist = (payload.artist or "").strip()
    album = (payload.album or "").strip()
    if title or artist or album:
        query = replace(
            query,
            title=title or query.title,
            artist=artist or None,
            album=album or None,
        )
    if not query.title.strip():
        raise HTTPException(status_code=400, detail="请先填写歌曲名再搜索")

    names = [source] if source else [
        name for name in SCRAPE_SOURCES if name in runtime.metadata_sources
    ]
    limit = max(1, min(payload.limit, 20))
    batches = await asyncio.gather(
        *(_search_one(context, runtime, track, name, query, limit) for name in names)
    )
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for batch in batches:
        for item in batch:
            meta = item["metadata"]
            key = (
                str(meta.get("title") or "").casefold(),
                str(meta.get("artist") or "").casefold(),
                str(meta.get("album") or "").casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
    results.sort(key=lambda item: item["score"], reverse=True)
    return {
        "path": track.path,
        "query": {
            "title": query.title,
            "artist": query.artist,
            "album": query.album,
        },
        "sources": _sources_payload(context, runtime),
        "searched": names,
        "results": results,
    }


async def _lyrics_for(context: AppContext, meta: TrackMetadata) -> str | None:
    """按候选自带的 id 去对应音源取歌词；没有 id 就不查，绝不乱猜。"""
    if not meta.external_id or meta.source not in SCRAPE_SOURCES:
        return None
    if meta.source not in context.metadata.providers:
        return None
    try:
        return clean_lyrics(await context.metadata.lyrics_for(meta))
    except Exception:  # noqa: BLE001 - 歌词拿不到不影响写标签
        return None


@router.post("/apply")
async def library_apply(
    payload: LibraryScrapeApply,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """手动刮削第二步：把选中的候选写进标签，只补空字段，不动文件位置。"""
    runtime = context.settings.get()
    track = await asyncio.to_thread(_track_or_404, context, payload.path)
    meta = candidate_from_dict(payload.metadata or {})
    if not meta.title.strip():
        raise HTTPException(status_code=400, detail="候选缺少歌曲名")
    # 歌词是加分项，但要单独点「抓取歌词」才去请求音源：
    # 用户只想要封面/专辑信息时，不该顺带对第三方多发一次请求。
    if payload.fetch_lyrics and not (meta.lyrics or "").strip():
        fetched = await _lyrics_for(context, meta)
        if fetched:
            meta = replace(meta, lyrics=fetched)
    result = await context.library.fill_track(
        track,
        remote=meta,
        runtime=runtime,
        with_lyrics=payload.with_lyrics,
        write=not payload.dry_run,
    )
    missing = missing_fields(
        replace(
            track.metadata,
            **{
                key: value
                for key, value in {
                    "title": meta.title or track.metadata.title,
                    "artist": track.metadata.artist or meta.artist,
                    "album": track.metadata.album or meta.album,
                    "album_artist": track.metadata.album_artist or meta.album_artist,
                    "year": track.metadata.year or meta.year,
                    "track_number": track.metadata.track_number or meta.track_number,
                }.items()
            },
            lyrics=track.metadata.lyrics or meta.lyrics,
        ),
        has_cover=track.has_cover,
    )
    return {
        "path": track.path,
        "dry_run": payload.dry_run,
        "written": result.get("written", []),
        "written_labels": [
            FIELD_LABELS.get(name, name) for name in result.get("written") or []
        ],
        "would_write": result.get("would_write") or [],
        "would_write_labels": [
            FIELD_LABELS.get(name, name) for name in result.get("would_write") or []
        ],
        "skipped": result.get("skipped", ""),
        "moved": False,
        "still_missing": missing,
        "still_missing_labels": [FIELD_LABELS.get(name, name) for name in missing],
        "message": _apply_message(result, payload.dry_run),
    }


def _apply_message(result: dict[str, Any], dry_run: bool) -> str:
    written = result.get("written") or result.get("would_write") or []
    if written:
        labels = "、".join(FIELD_LABELS.get(name, name) for name in written)
        return f"{'将写入' if dry_run else '已补全'}：{labels}（文件位置未改动）"
    reason = {
        "no_gap": "该候选没有能补的字段",
        "dry_run": "预览模式，未写入",
        "write_disabled": "当前设置关闭了「写入音频标签」",
        "write_failed": "写标签失败，格式可能不支持",
    }.get(str(result.get("skipped") or ""), "没有可写入的字段")
    return reason


@router.put("/lyrics")
async def library_lyrics(
    payload: LibraryLyricsApply,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """只替换歌词标签（手动刮削里「仅更新歌词」用）。"""
    runtime = context.settings.get()
    track = await asyncio.to_thread(_track_or_404, context, payload.path)
    text = clean_lyrics(payload.lyrics)
    if not text:
        raise HTTPException(status_code=400, detail="歌词为空")
    wanted = {"lyrics": text}
    if not runtime.write_tags and not payload.dry_run:
        raise HTTPException(status_code=400, detail="当前设置关闭了「写入音频标签」")
    written = (
        []
        if payload.dry_run
        else await write_missing_tags(Path(track.path), wanted, write_lyrics=True)
    )
    return {
        "path": track.path,
        "dry_run": payload.dry_run,
        "written": written,
        "moved": False,
        "message": "歌词已写入（文件位置未改动）" if written else "未写入歌词",
    }


@router.post("/scan")
async def library_scan(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    """查漏补缺的预览：只报告缺什么，不写任何东西。"""
    runtime = context.settings.get()
    listing = await asyncio.to_thread(context.library.list_tracks, runtime)
    incomplete = [item for item in listing["tracks"] if item["missing"]]
    return {
        "library_dir": listing["library_dir"],
        "total": listing["total"],
        "incomplete": len(incomplete),
        "tracks": [
            {
                "path": item["path"],
                "name": item["name"],
                "title": item["title"],
                "artist": item["artist"],
                "album": item["album"],
                "missing": item["missing"],
                "missing_labels": [FIELD_LABELS.get(name, name) for name in item["missing"]],
            }
            for item in incomplete
        ],
        "warnings": listing["warnings"],
    }


@router.post("/fill")
async def library_fill(
    payload: LibraryFillRequest,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """自动扫描查漏补缺：补全元数据，绝不移动 / 复制 / 硬链接 / 软链接文件。"""
    runtime = context.settings.get()
    sources = [name for name in payload.sources if name in SCRAPE_SOURCES]
    if not sources:
        sources = [name for name in runtime.metadata_sources if name in SCRAPE_SOURCES]
    if not payload.dry_run and not runtime.write_tags:
        raise HTTPException(status_code=400, detail="当前设置关闭了「写入音频标签」")
    # 补全与「转移方式」无关：这里既不移动也不复制，更不会建链接。
    note = "补全只改标签；文件位置、目录结构与链接关系一律不变。"

    usable = {
        name: context.metadata.providers[name]
        for name in sources
        if name in context.metadata.providers
    }
    # 与设置里的「在线匹配置信度阈值」同源，但至少要求 7/10 才敢往标签里写
    floor = max(7, round(runtime.confidence_threshold * 10))

    async def resolve_one(track: LibraryTrack) -> TrackMetadata | None:
        best = await context.library.best_candidate(track, providers=usable)
        if best is None:
            return None
        candidate, score = best
        return candidate if score >= floor else None

    results = await context.library.fill_batch(
        runtime,
        resolve_one=resolve_one,
        only_paths=payload.paths or None,
        limit=payload.limit,
        with_lyrics=payload.with_lyrics,
        dry_run=payload.dry_run,
    )
    filled = [row for row in results if row.get("written") or row.get("would_write")]
    return {
        "dry_run": payload.dry_run,
        "sources": sources,
        "checked": len(results),
        "filled": len(filled),
        "skipped": len(results) - len(filled),
        "moved": 0,
        "note": note,
        "results": results,
        "message": (
            f"扫描 {len(results)} 首待补全曲目，"
            f"{'可补全' if payload.dry_run else '已补全'} {len(filled)} 首，"
            "所有文件位置与链接关系保持不变"
        ),
    }
