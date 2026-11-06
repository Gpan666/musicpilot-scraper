"""The scraping pipeline: scan -> resolve metadata -> write tags -> transfer."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from musicpilot.adapters.metadata.service import MetadataService
from musicpilot.adapters.scanner import audio_counts, probe_file, scan_files
from musicpilot.adapters.store import Store
from musicpilot.adapters.tags import apply_tags
from musicpilot.config import RuntimeSettings, Settings
from musicpilot.domain.models import ItemStatus, JobKind, PlanItem, TrackMetadata
from musicpilot.domain.naming import target_path
from musicpilot.domain.transfer import TransferMode, transfer

ProgressCallback = Callable[[int, int, int, str, str], None]

# 文件名里不塞轨号的模板变量：轨号只进标签，歌名保持干净。
_TRACK_FIELDS = ("track", "disc")

# 单曲去掉轨号后若模板被削空，退化成「歌手 - 歌名」，与「歌手 / 单曲」预设一致
_SINGLE_FALLBACK_TEMPLATE = "{artist} - {title}"

_AUDIO_SUFFIXES = frozenset(
    {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif", ".dsf", ".dff", ".wma", ".ape"}
)

# Human choices made on the preview page: {source path: metadata overrides}.
ChoiceMap = dict[str, dict[str, Any]]

_PICK_FIELDS = ("title", "artist", "album", "album_artist", "year", "track_number")


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
    album_artist = str(choice.get("album_artist") or "").strip()
    values["album_artist"] = album_artist or values.get("artist")
    values["cover_url"] = str(choice.get("cover_url") or "") or None
    return TrackMetadata(**values)


def _drop_track_fields(template: str) -> str:
    """把模板里的 ``{track}`` / ``{disc}`` 去掉，再收拾残留的分隔符。

    ``{album}/{album_artist}/{track:02d} - {title}`` → ``{album}/{album_artist}/{title}``；
    ``{artist} - {title}`` 这种不含轨号的模板原样返回。
    """
    if not any("{" + name in template for name in _TRACK_FIELDS):
        return template
    cleaned = template
    for name in _TRACK_FIELDS:
        cleaned = re.sub(r"\{" + name + r"(:[^}]*)?\}", "", cleaned)
    segments = []
    for segment in cleaned.split("/"):
        segment = re.sub(r"^[\s\-_.]+", "", segment)
        segment = re.sub(r"[\s\-_.]+$", "", segment)
        segment = re.sub(r"\s{2,}", " ", segment)
        if segment:
            segments.append(segment)
    return "/".join(segments) or _SINGLE_FALLBACK_TEMPLATE


def _album_siblings(path: Path, root: Path) -> bool:
    """同级目录里还有别的音频文件，就认为它在专辑里。"""
    try:
        parent = path.parent.resolve()
        if parent == Path(root).resolve():
            return False
        for sibling in parent.iterdir():
            if sibling.is_file() and sibling.suffix.lower() in _AUDIO_SUFFIXES:
                if sibling.resolve() != path.resolve():
                    return True
        return False
    except OSError:
        return False


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
        # 同目录音频数只统计一次：单曲判定（目录内仅 1 个音频）靠它，
        # 不能为每个文件都去 listdir 一遍。
        counts = audio_counts(paths)

        def probe_batch() -> list[Any]:
            collected: list[Any] = []
            for path in paths:
                try:
                    collected.append(
                        probe_file(
                            path, root=source_root, sibling_audio_count=counts.get(path.parent)
                        )
                    )
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
                            detail="处理出错，详见日志",
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
                reason="采用预览页选择",
                source=meta.source,
            )
        local = audio.local_metadata
        # 单曲（同级没有别的音轨、也不知道自己在专辑里第几首）不该带轨号，
        # 否则每首歌的文件名都是「00 - 歌名」。专辑照旧按轨号排。
        in_album = await asyncio.to_thread(_album_siblings, Path(audio.path), runtime.source_root)
        track_number = meta.track_number if in_album and meta.track_number else None
        name_meta = replace(
            meta,
            track_number=track_number,
            disc_number=meta.disc_number if track_number else None,
        )
        template = runtime.naming_template
        if not in_album:
            template = _drop_track_fields(template)
        try:
            destination = target_path(
                library_root,
                template,
                name_meta,
                extension=audio.extension,
                source_filename=audio.display_name,
                unknown=runtime.unknown_placeholder,
            )
        except ValueError:
            return ProcessResult(
                PlanItem(
                    source=audio.path,
                    target="",
                    mode=mode.value,
                    status=ItemStatus.FAILED.value,
                    confidence=resolution.confidence,
                    detail="无法生成目标路径，请检查命名模板",
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
            album_artist=meta.album_artist or meta.artist,
            year=meta.year,
            track_number=meta.track_number,
            local_title=local.title,
            local_artist=local.artist,
            local_album=local.album,
            local_album_artist=local.album_artist or local.artist,
            local_year=local.year,
            local_track_number=local.track_number,
            local_lyrics=local.lyrics,
            has_local_lyrics=bool((local.lyrics or "").strip()),
            has_lyrics=bool((meta.lyrics or "").strip()),
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
                    detail="匹配度不足，已跳过",
                ),
                (0, 0, 1),
            )

        written: list[str] = []
        has_lyrics = False
        if runtime.write_tags and meta.title.strip():
            effective = meta
            if runtime.write_lyrics:
                # 搜索接口不一定带歌词，各音源都有独立的歌词接口，这里补一次
                lyrics = await self.metadata.lyrics_for(meta)
                if lyrics:
                    effective = replace(effective, lyrics=lyrics)
                if (effective.lyrics or "").strip():
                    has_lyrics = True
                elif meta.source not in {"", "local", "manual"}:
                    # 只有查了在线音源才谈得上「没取到」，本地标签本来就带不出歌词
                    resolution = replace(
                        resolution,
                        reason=f"{resolution.reason}（未取到歌词）",
                    )
            cover = None
            if runtime.embed_cover and not audio.has_embedded_cover:
                cover = await self.metadata.cover(meta.cover_url)
            try:
                written = await apply_tags(
                    Path(audio.path),
                    effective,
                    cover=cover,
                    lyrics_enabled=runtime.write_lyrics,
                    track_number=track_number,
                )
            except OSError as exc:
                written = []
                has_lyrics = False
                resolution = replace(resolution, reason=f"写标签失败：{exc}")

        # 说明列只放一两句中文摘要，细节见日志与悬停提示。
        tag_note = ""
        if runtime.write_tags:
            tag_note = "已写标签。" if written else "标签未写入（格式不支持或无可用字段）。"

        if not request.organize:
            return ProcessResult(
                replace(
                    base_item,
                    status=ItemStatus.DONE.value,
                    has_lyrics=has_lyrics,
                    detail=f"{tag_note}未整理文件。{resolution.reason}",
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
                has_lyrics=has_lyrics,
            ),
            counts,
        )
