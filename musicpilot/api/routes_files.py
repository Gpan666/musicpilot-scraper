from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from musicpilot.adapters.scanner import probe_file, read_tags, scan
from musicpilot.api.context import AppContext, get_context
from musicpilot.api.schemas import FilePathPayload
from musicpilot.domain.matcher import parse_filename
from musicpilot.domain.naming import target_path

router = APIRouter(prefix="/files", tags=["files"])


def _in_source(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve()) and path.is_file()
    except OSError:
        return False


def _browse_roots(context: AppContext) -> list[Path]:
    """Only these directories may be explored by the folder picker."""
    runtime = context.settings.get()
    candidates = list(context.base.browsable_paths)
    for configured in (runtime.source_root, runtime.library_root):
        parent = configured.parent
        # Never allow the whole filesystem as a browse root.
        candidates.append(parent if str(parent) != "/" else configured)
    resolved: list[Path] = []
    for candidate in candidates:
        try:
            target = candidate.resolve()
        except OSError:
            continue
        if target not in resolved:
            resolved.append(target)
    return resolved


def _within(path: Path, roots: list[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


@router.get("")
async def list_files(context: AppContext = Depends(get_context)) -> dict[str, Any]:
    runtime = context.settings.get()
    files, warnings = await asyncio.to_thread(
        scan,
        runtime.source_root,
        extensions=context.base.extensions,
        exclude_dirs=context.base.excluded_directories,
        max_files=context.base.max_scan_files,
    )
    return {
        "source_dir": str(runtime.source_root),
        "total": len(files),
        "warnings": warnings,
        "files": [item.as_dict() for item in files[:1000]],
        "truncated": len(files) > 1000,
    }


@router.get("/directories")
async def list_directories(
    path: str = Query(default="", max_length=2000),
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    """Browse server-side folders for the directory picker.

    Access is confined to configured browse roots (``MP_BROWSABLE_ROOTS``) plus
    the parents of the currently configured source/library directories. Symlinks
    escaping those roots are never exposed.
    """
    roots = _browse_roots(context)
    if not roots:
        raise HTTPException(status_code=403, detail="没有可用的浏览根目录")
    requested = Path(path) if path else roots[0]
    if not requested.is_absolute():
        raise HTTPException(status_code=400, detail="请使用绝对路径")
    try:
        current = requested.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="路径无效") from exc
    if not _within(current, roots):
        raise HTTPException(status_code=403, detail="目录不在允许的挂载范围内")
    if not current.is_dir():
        raise HTTPException(status_code=404, detail="目录不存在")

    excluded = context.base.excluded_directories
    entries: list[dict[str, Any]] = []
    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.lower())
    except OSError as exc:
        raise HTTPException(status_code=403, detail="无法读取该目录") from exc
    for child in children:
        if child.name.startswith(".") or child.name in excluded:
            continue
        try:
            if not child.is_dir():
                continue
            target = child.resolve()
            if not _within(target, roots):
                continue
            entries.append({"name": child.name, "path": str(target)})
        except OSError:
            continue

    parent = current.parent
    containing_root = next(
        (str(root) for root in roots if current == root or current.is_relative_to(root)),
        str(roots[0]),
    )
    return {
        "path": str(current),
        "parent": str(parent) if parent != current and _within(parent, roots) else None,
        "root": containing_root,
        "entries": entries[:500],
        "truncated": len(entries) > 500,
    }


@router.post("/detail")
async def file_detail(
    payload: FilePathPayload,
    context: AppContext = Depends(get_context),
) -> dict[str, Any]:
    runtime = context.settings.get()
    path = Path(payload.path)
    if not _in_source(path, runtime.source_root):
        raise HTTPException(status_code=400, detail="文件不在源目录内或不存在")
    audio = await asyncio.to_thread(probe_file, path, root=runtime.source_root)
    tagged, has_cover, technical = await asyncio.to_thread(read_tags, path)
    guessed = parse_filename(path, root=runtime.source_root)
    try:
        preview = str(
            target_path(
                runtime.library_root,
                runtime.naming_template,
                audio.local_metadata,
                extension=audio.extension,
                source_filename=audio.display_name,
                unknown=runtime.unknown_placeholder,
            )
        )
    except ValueError as exc:
        preview = f"无法生成：{exc}"
    return {
        "file": audio.as_dict(),
        "embedded_tags": tagged.as_dict(),
        "filename_guess": guessed.as_dict(),
        "has_embedded_cover": has_cover,
        "technical": technical,
        "preview_target": preview,
    }
