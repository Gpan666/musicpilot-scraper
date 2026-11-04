from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

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
