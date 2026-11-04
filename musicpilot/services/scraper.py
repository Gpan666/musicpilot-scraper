"""The scraping pipeline: scan -> resolve metadata -> write tags -> transfer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from musicpilot.adapters.metadata.service import MetadataService
from musicpilot.adapters.scanner import probe_file, scan_files
from musicpilot.adapters.store import Store
from musicpilot.adapters.tags import apply_tags
from musicpilot.config import RuntimeSettings, Settings
from musicpilot.domain.models import ItemStatus, JobKind, PlanItem, TrackMetadata
from musicpilot.domain.naming import target_path
from musicpilot.domain.transfer import TransferMode, transfer

ProgressCallback = Callable[[int, int, int, str, str], None]

# Human choices made on the preview page: {source path: metadata overrides}.
ChoiceMap = dict[str, dict[str, Any]]

_PICK_FIELDS = ("title", "artist", "album", "year", "track_number")


def _picked_metadata(choice: dict[str, Any]) -> TrackMetadata:
    """Turn a raw override payload into trusted ``TrackMetadata``."""
    values: dict[str, Any] = {}
    for name in _PICK_FIELDS:
        value = choice.get(name)
        if value is None:
            continue
        if name in {"year", "track_number"}:
            try:
                values[name] = int(value) if str(value).strip() else None
            except (TypeError, ValueError):
                values[name] = None
        else:
            values[name] = str(value).strip() or None
    values.setdefault("title", "")
    source = str(choice.get("source") or "manual").strip() or "manual"
    values["source"] = source
    values["external_id"] = str(choice.get("external_id") or "") or None
    values["album_artist"] = values.get("artist")
    values["cover_url"] = str(choice.get("cover_url") or "") or None
    return TrackMetadata(**values)


@dataclass(frozen=True, slots=True)
class JobRequest:
    kind: JobKind
    dry_run: bool = False
    paths: list[str] | None = None
    organize: bool = True
    skip_low_confidence: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "dry_run": self.dry_run,
            "organize": self.organize,
            "skip_low_confidence": self.skip_low_confidence,
            "paths": self.paths or [],
        }


@dataclass(frozen=True, slots=True)
class ProcessResult:
    item: PlanItem
    counts: tuple[int, int, int]  # done, failed, skipped


class ScraperService:
    def __init__(
        self,
        base: Settings,
        store: Store,
        metadata: MetadataService,
    ) -> None:
        self.base = base
        self.store = store
        self.metadata = metadata

    async def execute(
        self,
        job_id: str,
        request: JobRequest,
        runtime: RuntimeSettings,
        *,
        progress: ProgressCallback,
        cancel_event: asyncio.Event,
        choices: ChoiceMap | None = None,
    ) -> list[str]:
        """Run the whole batch; return human-readable warnings for the job message."""
        source_root = runtime.source_root
        library_root = runtime.library_root
        warnings: list[str] = []

        if not source_root.is_dir():
            raise ValueError(f"源目录不存在：{source_root}")
        if request.organize and not request.dry_run:
            library_root.mkdir(parents=True, exist_ok=True)

        paths, scan_warnings = self._collect_paths(runtime, request.paths)
        warnings.extend(scan_warnings)
        def probe_batch() -> list[Any]:
            collected: list[Any] = []
            for path in paths:
                try:
                    collected.append(probe_file(path, root=source_root))
                except OSError as exc:
                    warnings.append(f"跳过无法读取的文件 {path.name}：{exc}")
            return collected

        files = await asyncio.to_thread(probe_batch)
        if not files:
            progress(0, 0, 0, "", "没有找到可处理的音频文件")
            return warnings + ["没有找到可处理的音频文件"]

        done, failed, skipped = 0, 0, 0
        progress(0, 0, 0, "", f"已扫描到 {len(files)} 个文件")
        if request.kind is JobKind.AUTO and len(files) > 1:
            self.store.update_progress(job_id, total=len(files))
        chunk_size = max(1, min(self.base.scan_concurrency, 8))
        for offset in range(0, len(files), chunk_size):
            if cancel_event.is_set():
                warnings.append("任务被取消，剩余文件未处理")
                break
            chunk = files[offset : offset + chunk_size]
            results = await asyncio.gather(
                *(
                    self._process(
                        audio,
                        request,
                        runtime,
                        library_root,
                        (choices or {}).get(audio.path),
                    )
                    for audio in chunk
                ),
                return_exceptions=True,
            )
            batch: list[PlanItem] = []
            for audio, result in zip(chunk, results, strict=True):
                if isinstance(result, BaseException):
                    failed += 1
                    batch.append(
                        PlanItem(
                            source=audio.path,
                            target="",
                            mode=runtime.transfer_mode.value,
                            status=ItemStatus.FAILED.value,
                            detail=f"处理异常：{result}",
                        )
                    )
                    continue
                batch.append(result.item)
                d, f, s = result.counts
                done += d
                failed += f
                skipped += s
            self.store.add_items(job_id, batch)
            processed = min(offset + len(chunk), len(files))
            progress(
                processed,
                failed,
                skipped,
                "",
                f"已处理 {processed}/{len(files)}",
            )
        return warnings

    def _collect_paths(
        self, runtime: RuntimeSettings, requested: list[str] | None
    ) -> tuple[list[Path], list[str]]:
        root = runtime.source_root.resolve()
        warnings: list[str] = []
        if requested:
            paths: list[Path] = []
            for value in requested:
                try:
                    candidate = Path(value).resolve()
                except OSError:
                    continue
                if not candidate.is_relative_to(root):
                    warnings.append(f"已拒绝越出源目录的路径：{value}")
                    continue
                if candidate.is_file():
                    paths.append(candidate)
                else:
                    warnings.append(f"文件不存在，已跳过：{value}")
            return sorted(set(paths)), warnings
        return scan_files(
            runtime.source_root,
            extensions=self.base.extensions,
            exclude_dirs=self.base.excluded_directories,
            max_files=self.base.max_scan_files,
        )

    async def _process(
        self,
        audio: Any,
        request: JobRequest,
        runtime: RuntimeSettings,
        library_root: Path,
        choice: dict[str, Any] | None = None,
    ) -> ProcessResult:
        mode: TransferMode = runtime.transfer_mode
        resolution = await self.metadata.resolve(audio, runtime)
        meta = resolution.metadata
        if choice:
            meta = replace(_picked_metadata(choice), lyrics=meta.lyrics)
            resolution = replace(
                resolution,
                metadata=meta,
                confidence=1.0,
                needs_review=False,
                reason=f"按预览页的人工选择写入（候选来自 {meta.source}）",
                source=meta.source,
            )
        local = audio.local_metadata
        try:
            destination = target_path(
                library_root,
                runtime.naming_template,
                meta,
                extension=audio.extension,
                source_filename=audio.display_name,
                unknown=runtime.unknown_placeholder,
            )
        except ValueError as exc:
            return ProcessResult(
                PlanItem(
                    source=audio.path,
                    target="",
                    mode=mode.value,
                    status=ItemStatus.FAILED.value,
                    confidence=resolution.confidence,
                    detail=f"无法生成目标路径：{exc}",
                ),
                (0, 1, 0),
            )

        base_item = PlanItem(
            source=audio.path,
            target=str(destination),
            mode=mode.value,
            status=ItemStatus.PLANNED.value,
            confidence=resolution.confidence,
            source_name=resolution.source,
            title=meta.title,
            artist=meta.artist,
            album=meta.album,
            year=meta.year,
            track_number=meta.track_number,
            local_title=local.title,
            local_artist=local.artist,
            local_album=local.album,
            local_year=local.year,
            local_track_number=local.track_number,
            cover_url=meta.cover_url,
            candidates=[item.as_dict() for item in resolution.candidates],
        )

        if request.dry_run:
            status = (
                ItemStatus.NEEDS_REVIEW if resolution.needs_review else ItemStatus.PLANNED
            ).value
            suffix = "" if request.organize else "（仅写标签，不整理文件）"
            return ProcessResult(
                replace(base_item, status=status, detail=f"预览：{resolution.reason}{suffix}"),
                (0, 0, 0),
            )

        if resolution.needs_review and request.skip_low_confidence:
            return ProcessResult(
                replace(
                    base_item,
                    status=ItemStatus.SKIPPED.value,
                    detail=f"置信度不足，按选项跳过：{resolution.reason}",
                ),
                (0, 0, 1),
            )

        written: list[str] = []
        if runtime.write_tags and meta.title.strip():
            effective = meta
            if runtime.write_lyrics:
                lyrics = await self.metadata.lyrics_for(meta)
                if lyrics:
                    effective = replace(effective, lyrics=lyrics)
            cover = None
            if runtime.embed_cover and not audio.has_embedded_cover:
                cover = await self.metadata.cover(meta.cover_url)
            try:
                written = await apply_tags(
                    Path(audio.path),
                    effective,
                    cover=cover,
                    lyrics_enabled=runtime.write_lyrics,
                )
            except OSError as exc:
                written = []
                resolution = replace(resolution, reason=f"{resolution.reason}；写标签失败：{exc}")

        tag_note = ""
        if runtime.write_tags:
            if written:
                tag_note = f"已写入标签 {', '.join(written)}。"
            else:
                tag_note = "标签未写入（文件格式不支持或没有可写字段）。"
            if mode in {TransferMode.HARDLINK, TransferMode.SYMLINK} and written:
                tag_note += "链接共享源文件数据，源文件的标签同样被补全。"

        if not request.organize:
            return ProcessResult(
                replace(
                    base_item,
                    status=ItemStatus.DONE.value,
                    detail=f"{tag_note}本次未整理文件。{resolution.reason}",
                ),
                (1, 0, 0),
            )

        result = await asyncio.to_thread(
            transfer,
            Path(audio.path),
            destination,
            mode=mode,
            conflict_policy=runtime.conflict_policy,
            symlink_relative=runtime.symlink_relative,
            allowed_root=library_root,
        )
        status = {
            "transferred": ItemStatus.DONE,
            "skipped": ItemStatus.SKIPPED,
            "failed": ItemStatus.FAILED,
        }[result.status]
        detail = f"{tag_note}{result.detail}。{resolution.reason}".strip()
        counts = (
            (1, 0, 0) if status is ItemStatus.DONE
            else (0, 0, 1) if status is ItemStatus.SKIPPED
            else (0, 1, 0)
        )
        return ProcessResult(
            replace(
                base_item,
                target=result.target or str(destination),
                status=status.value,
                detail=detail,
                link_target=result.link_target,
            ),
            counts,
        )
