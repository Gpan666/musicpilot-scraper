"""音乐库：枚举磁盘上的文件、读标签、只改元数据的查漏补缺。

三条硬规矩，写在代码里也写在界面上：

* 列表**每次**都直接遍历音乐库目录，不读数据库快照——库里有什么就显示什么。
* 补全只写标签，**不移动、不复制、不建硬链接/软链接**。路径与链接关系一律不改。
* 只补空字段，文件里已有的标签一个都不覆盖。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from musicpilot.adapters.scanner import audio_counts, audio_in_dir, read_tags, scan_files, with_kind
from musicpilot.adapters.tags import write_missing_tags
from musicpilot.config import RuntimeSettings, Settings
from musicpilot.domain.library import match_score, missing_fields, plan_fill
from musicpilot.domain.models import TrackMetadata

# 标签预览里最多带多少字歌词；全文走详情接口。
LYRICS_PREVIEW_CHARS = 120
# 单次列表最多展示多少首，超出的提示用户用筛选。
LIST_LIMIT = 2000


@dataclass(frozen=True, slots=True)
class LibraryTrack:
    path: str
    relative_dir: str
    name: str
    extension: str
    size: int
    modified_at: float
    is_symlink: bool
    metadata: TrackMetadata
    has_cover: bool
    duration: float | None
    missing: list[str] = field(default_factory=list)
    # 单曲 / 专辑曲目，与 metadata.kind 一致；单独放一份，前端不用再往里钻
    kind: str = "album"
    kind_reason: str = ""

    def as_dict(self, *, lyrics_preview: int = LYRICS_PREVIEW_CHARS) -> dict[str, Any]:
        meta = self.metadata
        lyrics = (meta.lyrics or "").strip()
        return {
            "path": self.path,
            "name": self.name,
            "relative_dir": self.relative_dir,
            "extension": self.extension,
            "size": self.size,
            "modified_at": self.modified_at,
            "is_symlink": self.is_symlink,
            "duration": self.duration,
            "has_cover": self.has_cover,
            "cover_url": f"/api/library/cover?path={_q(self.path)}" if self.has_cover else None,
            "title": meta.title or Path(self.name).stem,
            "artist": meta.artist,
            "album": meta.album,
            "album_artist": meta.album_artist,
            "year": meta.year,
            "track_number": meta.track_number,
            "disc_number": meta.disc_number,
            "has_lyrics": bool(lyrics),
            "lyrics_preview": _preview(lyrics, lyrics_preview),
            "missing": list(self.missing),
            "complete": not self.missing,
            "kind": self.kind,
            "kind_reason": self.kind_reason,
        }

    def detail(self) -> dict[str, Any]:
        payload = self.as_dict(lyrics_preview=0)
        payload["lyrics"] = self.metadata.lyrics or ""
        payload["metadata"] = self.metadata.as_dict()
        return payload


def _preview(text: str, limit: int) -> str:
    if not text or not limit:
        return ""
    body = " ".join(line.strip() for line in text.splitlines() if line.strip())
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _q(value: str) -> str:
    return quote(value, safe="")


# 没拿到配置时的兜底音频后缀：只用来数「同目录有几首」，不做别的判断
_FALLBACK_AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif",
     ".dsf", ".dff", ".wma", ".ape"}
)


def read_track(
    path: Path,
    *,
    root: Path,
    extensions: frozenset[str] | None = None,
    sibling_audio_count: int | None = None,
) -> LibraryTrack:
    meta, has_cover, technical = read_tags(path)
    # 没给同目录音频数就只数这一个目录（单首重读用，不整库遍历）
    if sibling_audio_count is None:
        sibling_audio_count = audio_in_dir(
            path.parent, extensions=extensions or _FALLBACK_AUDIO_EXTENSIONS
        )
    meta = with_kind(meta, sibling_audio_count, technical.get("album_type"))
    stat = path.stat()
    try:
        relative = path.parent.resolve().relative_to(root.resolve())
        relative_dir = "" if str(relative) == "." else str(relative)
    except (OSError, ValueError):
        relative_dir = str(path.parent)
    return LibraryTrack(
        path=str(path),
        relative_dir=relative_dir,
        name=path.name,
        extension=path.suffix.lower(),
        size=stat.st_size,
        modified_at=stat.st_mtime,
        is_symlink=path.is_symlink(),
        metadata=meta,
        has_cover=has_cover,
        duration=technical.get("duration"),
        missing=missing_fields(meta, has_cover=has_cover),
        kind=meta.kind or "album",
        kind_reason=meta.kind_reason,
    )


class LibraryService:
    def __init__(self, base: Settings) -> None:
        self.base = base

    # ------------------------------------------------------------------ 枚举
    def _roots(self, runtime: RuntimeSettings) -> list[Path]:
        """音乐库目录；若它落在源目录里（少见但合法），两者都算。"""
        roots: list[Path] = []
        for candidate in (runtime.library_root,):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved not in roots:
                roots.append(resolved)
        return roots

    def enumerate_paths(self, runtime: RuntimeSettings) -> tuple[list[Path], list[str]]:
        paths: list[Path] = []
        warnings: list[str] = []
        roots = self._roots(runtime)
        if not roots:
            return [], [f"音乐库目录不存在：{runtime.library_root}"]
        for root in roots:
            if not root.is_dir():
                warnings.append(f"音乐库目录不存在：{root}")
                continue
            found, root_warnings = scan_files(
                root,
                extensions=self.base.extensions,
                exclude_dirs=self.base.excluded_directories,
                max_files=self.base.max_scan_files,
            )
            warnings.extend(root_warnings)
            paths.extend(found)
        # 同一次枚举里可能有重复（嵌套根目录），按真实路径去重
        unique: dict[str, Path] = {}
        for path in paths:
            try:
                key = str(path.resolve())
            except OSError:
                key = str(path)
            unique.setdefault(key, path)
        return sorted(unique.values(), key=lambda item: str(item).lower()), warnings

    def list_tracks(self, runtime: RuntimeSettings) -> dict[str, Any]:
        paths, warnings = self.enumerate_paths(runtime)
        # 同目录音频数只统计一次：单曲判定靠它，不能每首曲目都去 listdir 一遍
        counts = audio_counts(paths)
        tracks: list[LibraryTrack] = []
        for path in paths:
            try:
                tracks.append(
                    read_track(
                        path,
                        root=runtime.library_root,
                        extensions=self.base.extensions,
                        sibling_audio_count=counts.get(path.parent),
                    )
                )
            except OSError as exc:
                warnings.append(f"无法读取 {path.name}：{exc}")
        tracks.sort(key=lambda item: (item.relative_dir.lower(), item.name.lower()))
        items = [track.as_dict() for track in tracks[:LIST_LIMIT]]
        return {
            "library_dir": str(runtime.library_root),
            "total": len(tracks),
            "truncated": len(tracks) > LIST_LIMIT,
            "warnings": warnings,
            "stats": _stats(tracks),
            "tracks": items,
        }

    def find(self, path: str, runtime: RuntimeSettings) -> LibraryTrack:
        target = self.resolve(path, runtime)
        # 单首：只数它所在的那一个目录，不重扫整库
        return read_track(target, root=runtime.library_root, extensions=self.base.extensions)

    def resolve(self, path: str, runtime: RuntimeSettings) -> Path:
        """只接受音乐库目录内的真实文件，越界一律拒绝。"""
        root = runtime.library_root.resolve()
        target = Path(path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("文件不在音乐库目录内或不存在")
        return target

    # ------------------------------------------------------------------ 补全
    async def fill_track(
        self,
        track: LibraryTrack,
        *,
        remote: TrackMetadata,
        runtime: RuntimeSettings,
        with_lyrics: bool = True,
        write: bool = True,
    ) -> dict[str, Any]:
        """把 ``remote`` 里有的、文件里缺的字段写进标签，返回写入结果。

        绝不移动/复制/链接文件：只调用 ``write_missing_tags``，路径原样保留。
        """
        # 与界面预览同一个入口：预览说会写什么，这里就写什么
        wanted = plan_fill(track.metadata, remote, with_lyrics=with_lyrics)
        planned = list(wanted)
        if not wanted:
            return {"path": track.path, "written": [], "skipped": "no_gap", "moved": False}
        if not write:
            return {
                "path": track.path,
                "written": [],
                "would_write": planned,
                "skipped": "dry_run",
                "moved": False,
                "target": track.path,
            }
        if not runtime.write_tags:
            return {"path": track.path, "written": [], "skipped": "write_disabled", "moved": False}
        written = await write_missing_tags(
            Path(track.path), wanted, write_lyrics=bool(wanted.get("lyrics"))
        )
        return {
            "path": track.path,
            "written": written,
            "would_write": planned,
            "skipped": "" if written else "write_failed",
            "moved": False,
            "target": track.path,
        }

    async def fill_batch(
        self,
        runtime: RuntimeSettings,
        *,
        resolve_one: Any,
        only_paths: list[str] | None = None,
        limit: int | None = None,
        with_lyrics: bool = True,
        dry_run: bool = False,
    ) -> list[dict[str, Any]]:
        """扫描整库，把「缺字段」的曲目逐个补全。

        ``resolve_one`` 是协程函数 ``(track) -> TrackMetadata | None``，由调用方
        决定用哪个音源去查；本服务只负责写标签。
        """
        listing = await asyncio.to_thread(self.list_tracks, runtime)
        wanted = set(only_paths or [])
        targets = [
            item for item in listing["tracks"]
            if item["missing"] and (not wanted or item["path"] in wanted)
        ]
        if limit is not None:
            targets = targets[:limit]
        results: list[dict[str, Any]] = []
        for item in targets:
            track = await asyncio.to_thread(self.find, item["path"], runtime)
            try:
                remote = await resolve_one(track)
            except Exception:  # noqa: BLE001 - 单个失败不影响整批
                remote = None
            if remote is None:
                results.append(
                    {"path": track.path, "written": [], "skipped": "no_match", "moved": False}
                )
                continue
            results.append(
                await self.fill_track(
                    track,
                    remote=remote,
                    runtime=runtime,
                    with_lyrics=with_lyrics,
                    write=not dry_run,
                )
            )
        return results

    async def best_candidate(
        self,
        track: LibraryTrack,
        *,
        providers: dict[str, Any] | None,
        limit: int = 8,
    ) -> tuple[TrackMetadata, int] | None:
        """在全部已启用音源里挑匹配度最高的候选。

        返回 ``(候选, 匹配度 1–10)``；一个候选都没有就返回 ``None``。
        分数怎么用（要不要过阈值）交给调用方，好让界面能把「搜到了但不够像」说清楚。
        """
        query = track.metadata
        if not query.title.strip():
            from musicpilot.domain.matcher import parse_filename

            query = parse_filename(Path(track.path))
        ranked: list[tuple[int, TrackMetadata]] = []
        for provider in (providers or {}).values():
            search = getattr(provider, "search", None)
            if not callable(search):
                continue
            try:
                found = await search(
                    title=query.title, artist=query.artist, album=query.album, limit=5
                )
            except Exception:  # noqa: BLE001 - 单个音源失败不影响其它音源
                continue
            for candidate in found:
                ranked.append((match_score(query, candidate), candidate))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1], ranked[0][0]


def _stats(tracks: list[LibraryTrack]) -> dict[str, Any]:
    artists = {t.metadata.artist for t in tracks if t.metadata.artist}
    albums = {(t.metadata.album, t.metadata.album_artist) for t in tracks if t.metadata.album}
    singles = sum(1 for t in tracks if t.kind == "single")
    return {
        "tracks": len(tracks),
        "artists": len(artists),
        "albums": len(albums),
        "with_cover": sum(1 for t in tracks if t.has_cover),
        "with_lyrics": sum(1 for t in tracks if (t.metadata.lyrics or "").strip()),
        "incomplete": sum(1 for t in tracks if t.missing),
        # 单曲 / 专辑曲目分开计数：界面上说的是「单曲 N / 专辑曲目 M」，不是一句话糊过去
        "singles": singles,
        "album_tracks": len(tracks) - singles,
    }
