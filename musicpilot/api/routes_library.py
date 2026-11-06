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
    LibraryLyricsFetch,
    LibraryScrapeApply,
    LibrarySearchRequest,
)
from musicpilot.domain.library import FIELD_LABELS, match_score, missing_fields, plan_fill
from musicpilot.domain.models import TrackMetadata
from musicpilot.domain.transfer import mode_label
from musicpilot.services.library import LibraryTrack

router = APIRouter(prefix="/library", tags=["library"])

# 手动刮削可以选哪些来源：本地标签只做参考，不作为「下载来源」。
SCRAPE_SOURCES = ("musicbrainz", "netease", "qq", "kugou", "kuwo")


def _sources_payload(context: AppContext, runtime: Any) -> list[dict[str, Any]]:
    """每个源输出「启用 / 能不能搜 / 能不能取词 / 限制说明」，供前端置灰与提示。

    原则：不允许出现 chip 显示可用、实际搜不出东西的源。能用的就说能用，
    不能用的就把原因写在用户看得见的地方。
    """
    enabled = [name for name in runtime.metadata_sources if name in SCRAPE_SOURCES]
    status = context.metadata.source_status()
    payload: list[dict[str, Any]] = []
    for name in SCRAPE_SOURCES:
        info = status.get(name, {})
        available = bool(info.get("search", name in context.metadata.providers))
        lyrics = bool(info.get("lyrics"))
        search_note = str(info.get("search_note") or "")
        lyrics_note = str(info.get("lyrics_note") or "")
        note = "；".join(part for part in (search_note, lyrics_note) if part)
        payload.append(
            {
                "id": name,
                "label": SOURCE_LABELS.get(name, name),
                "enabled": name in enabled,
                "available": available,
                "lyrics": lyrics,
                "search_note": search_note,
                "lyrics_note": lyrics_note,
                "note": note,
            }
        )
    return payload


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
) -> dict[str, Any]:
    """搜一个源，并且把「搜到几条 / 这次有没有搜成」一起带回来。

    「可用但这次 0 条」和「接口挂了」在界面上要分得开：前者只提示本次没结果，
    后者才说明这个源现在不可用 —— 不能让 chip 亮着却悄悄空着。
    """
    provider = context.metadata.providers.get(source)
    label = SOURCE_LABELS.get(source, source)
    if provider is None:
        return {"source": source, "label": label, "ok": False, "items": [], "note": "该来源未就绪"}
    try:
        found = await provider.search(
            title=query.title,
            artist=query.artist,
            album=query.album,
            limit=limit,
        )
    except Exception:  # noqa: BLE001 - 网络/接口异常按「没搜到」处理
        return {
            "source": source,
            "label": label,
            "ok": False,
            "items": [],
            "note": "接口无返回",
        }
    supports = bool(getattr(provider, "supports_lyrics", False))
    return {
        "source": source,
        "label": label,
        "ok": True,
        "note": str(getattr(provider, "search_note", "") or ""),
        "items": [
            _candidate_payload(track, candidate, can_fetch_lyrics=supports)
            for candidate in found
        ],
    }


def _candidate_payload(
    track: LibraryTrack,
    candidate: TrackMetadata,
    *,
    can_fetch_lyrics: bool = False,
) -> dict[str, Any]:
    query = track.metadata
    if not query.title.strip():
        from musicpilot.domain.matcher import parse_filename

        query = parse_filename(Path(track.path))
    meta = candidate.as_dict()
    meta["lyrics"] = clean_lyrics(candidate.lyrics)
    planned = plan_fill(track.metadata, candidate)
    return {
        "score": match_score(query, candidate),
        # 「可补」= 这次真的会写进去的字段，与下面的将写入清单同源，不虚报封面 / 歌词
        "fills": list(planned),
        "metadata": meta,
        "source_label": SOURCE_LABELS.get(candidate.source, candidate.source),
        # 候选自带歌词就能直接填；没带但是支持按 id 取词的，前端可以点「尝试取词」
        "can_fetch_lyrics": bool(can_fetch_lyrics),
        # 预览窗的逐字段对照 + 将写入清单：与真正写入走同一个 plan_fill，说写才写
        "fields": _candidate_fields(track, candidate),
        "would_write": list(planned),
        "would_write_labels": [FIELD_LABELS.get(name, name) for name in planned],
        # 候选有没有随带歌词：没带就明说，别让用户以为「点了写入就一定有词」
        "has_lyrics": bool((candidate.lyrics or "").strip()),
    }


# 预览窗的对照表字段顺序：元数据在前，封面歌词垫底
_COMPARE_FIELDS = (
    ("title", "标题"),
    ("artist", "歌手"),
    ("album", "专辑"),
    ("album_artist", "专辑艺术家"),
    ("year", "年份"),
    ("track_number", "专辑轨号"),
    ("disc_number", "碟号"),
    ("cover", "封面"),
    ("lyrics", "歌词"),
)


def _candidate_fields(track: LibraryTrack, candidate: TrackMetadata) -> list[dict[str, Any]]:
    """逐字段对照：当前文件里的值 / 候选值 / 会不会写入。

    「会不会写入」一律以 ``plan_fill`` 为准：已有值就是不覆盖，空着才写。
    """
    planned = plan_fill(track.metadata, candidate)
    rows: list[dict[str, Any]] = []
    for key, label in _COMPARE_FIELDS:
        note = ""
        if key == "cover":
            current = "有封面" if track.has_cover else ""
            proposed = "有封面" if candidate.cover_url else ""
            # 库补全这条路只写标签文本，不内嵌封面：别让界面上写着「会写入封面」却不落盘
            will_write = False
            note = "补全只写标签文本，不内嵌封面"
        elif key == "lyrics":
            current = "有歌词" if (track.metadata.lyrics or "").strip() else ""
            proposed = "有歌词" if (candidate.lyrics or "").strip() else ""
            will_write = "lyrics" in planned
        else:
            current = getattr(track.metadata, key, None)
            proposed = getattr(candidate, key, None)
            will_write = key in planned
        rows.append(
            {
                "key": key,
                "label": label,
                "current": "" if current in (None, "") else str(current),
                "candidate": "" if proposed in (None, "") else str(proposed),
                "will_write": will_write,
                # 文件里已有值 → 后端只补空字段，界面写「不覆盖」而不是「会覆盖」
                "keep": bool(current) and not will_write,
                "note": note,
            }
        )
    return rows


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
    source_stats = [
        {
            "id": batch["source"],
            "label": batch["label"],
            "count": len(batch["items"]),
            "ok": batch["ok"],
            "note": batch.get("note", ""),
        }
        for batch in batches
    ]
    for batch in batches:
        for item in batch["items"]:
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
        "source_stats": source_stats,
        "results": results,
    }


async def _fetch_lyrics(context: AppContext, meta: TrackMetadata) -> Any:
    """按候选自带的 id 去对应音源取词；没有 id 就不查，绝不乱猜。"""
    if meta.source not in SCRAPE_SOURCES:
        from musicpilot.adapters.metadata.base import REASON_UNSUPPORTED, LyricsResult

        return LyricsResult(reason=REASON_UNSUPPORTED)
    return await context.metadata.fetch_lyrics(meta)


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
    lyrics_reason = ""
    if payload.fetch_lyrics and not (meta.lyrics or "").strip():
        fetched = await _fetch_lyrics(context, meta)
        if fetched.text:
            meta = replace(meta, lyrics=fetched.text)
        else:
            lyrics_reason = fetched.reason
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
        "lyrics_reason": lyrics_reason,
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


@router.post("/lyrics/fetch")
async def library_lyrics_fetch(
    payload: LibraryLyricsFetch,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """按候选 id 去对应音源取歌词正文：只读，绝不写文件、不动标签与位置。

    这是「从音源搜词」拿到正文的唯一通道：拿不到就老实说为什么拿不到，
    不编造歌词，也不把「拿不到」笼统说成「没有」。
    """
    source = (payload.source or "").strip().lower()
    if source not in SCRAPE_SOURCES:
        raise HTTPException(status_code=400, detail="不支持的来源平台")
    meta = TrackMetadata(
        title=(payload.title or "").strip(),
        album=(payload.album or "").strip() or None,
        source=source,
        external_id=payload.external_id.strip(),
    )
    result = await _fetch_lyrics(context, meta)
    return {
        "lyrics": clean_lyrics(result.text),
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source),
        "reason": "" if result.text else (result.reason or "该歌曲没有歌词"),
    }


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
    """查漏补缺的预览：只报告缺什么，不写任何东西。

    单曲与专辑曲目分开统计：单曲本来就没有专辑与专辑轨号，混在一起数，整屏都是
    「缺 专辑轨号」，看不出到底哪几首真的缺东西。
    """
    runtime = context.settings.get()
    listing = await asyncio.to_thread(context.library.list_tracks, runtime)
    incomplete = [item for item in listing["tracks"] if item["missing"]]
    singles = [item for item in incomplete if item["kind"] == "single"]
    album_tracks = [item for item in incomplete if item["kind"] != "single"]

    def row(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": item["path"],
            "name": item["name"],
            "title": item["title"],
            "artist": item["artist"],
            "album": item["album"],
            "kind": item["kind"],
            "kind_reason": item["kind_reason"],
            "missing": item["missing"],
            "missing_labels": [FIELD_LABELS.get(name, name) for name in item["missing"]],
        }

    return {
        "library_dir": listing["library_dir"],
        "total": listing["total"],
        "incomplete": len(incomplete),
        # 单曲 / 专辑曲目各缺几首，前端据此分成两句话说
        "singles": {"incomplete": len(singles), "total": listing["stats"].get("singles", 0)},
        "album_tracks": {
            "incomplete": len(album_tracks),
            "total": listing["stats"].get("album_tracks", 0),
        },
        "tracks": [row(item) for item in incomplete],
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
