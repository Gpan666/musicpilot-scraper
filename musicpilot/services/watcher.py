"""自动刮削：轮询源目录，新文件稳定后就自动排一个刮削任务。

用轮询而不是 inotify：容器里的源目录通常是 NFS / SMB / FUSE 挂载，
文件系统事件在这些场景下并不可靠，而一次目录遍历对个人音乐库来说足够便宜。

五条硬规则，缺一条就会「自动刮削一直触发，但库里什么都没多」：

1. **音乐库里已经有对应文件就算处理过**（核心）。自动任务只刮「库里还没有
   的音频」：按目标路径、文件名、文件大小三层判定。用复制 / 硬链接 / 软链接
   时源文件留在原地，只靠指纹判断会导致「刮完一遍、下一轮又刮一遍、再写一份」，
   任务记录一直涨而库里的文件没变化。
2. **已处理的文件写进 SQLite**（``watch_seen``），不只在内存里。进程重启、
   容器更新后仍然认得历史文件，不会把整个源目录当成新文件重刮一遍。
3. **开启监控的那一刻，源目录里已有的文件算作旧文件**，只记录不刮削。
   老文件想刮就手动点，或先「把现有文件标记为已处理」再放新文件。
4. **刮削中间产物（``.part`` / ``.!qB`` / ``.aria2`` …）等写完再说**，
   半成品文件不会被当成新文件反复触发任务。
5. **大小或修改时间变过的文件重新进入候选**——下载器续传落地后仍然会刮，
   只是被规则 1 挡住「库里已有」的那些。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from musicpilot.adapters.scanner import scan_files
from musicpilot.config import RuntimeSettings
from musicpilot.domain.models import JobKind
from musicpilot.services.jobs import JobConflict, JobManager
from musicpilot.services.scraper import JobRequest
from musicpilot.services.settings_service import SettingsError, SettingsService

logger = logging.getLogger("musicpilot.watcher")

# 每次最多排这么多文件，避免一次性把上千个文件塞进一个任务
MAX_BATCH = 500
# 已处理指纹上限，与 Store._MAX_SEEN 对应
_MAX_SEEN = 200_000
# 音乐库索引上限，防止超大曲库每次扫描都拖慢轮询
_LIBRARY_SCAN_LIMIT = 200_000
# 下载器的临时文件后缀：还没写完，等它变成正式文件名再说
_PARTIAL_SUFFIXES = (
    ".part",
    ".partial",
    ".!qb",
    ".aria2",
    ".crdownload",
    ".download",
    ".tmp",
    ".temp",
    ".filepart",
)


@dataclass(frozen=True, slots=True)
class LibraryEntry:
    """One audio file already sitting in the music library."""

    path: str
    name: str
    size: int
    mtime: float


class LibraryIndex:
    """音乐库里已有的音频文件，用来判断「这个文件已经刮过了」。

    命中任意一条就算「刮过了」，都不看修改时间——写标签会改动源文件的 mtime，
    时间戳在这里没有意义：

    * **同名**：库里有同名文件。
    * **后缀**：源文件与库内文件名（去扩展名）互为后缀，
      ``01 - Bohemian Rhapsody.flac`` 对得上 ``01 - Queen - Bohemian Rhapsody.flac``。
    * **顺序**：库里同扩展名的文件数与源目录一致（整批整理完、命名模板大改），
      排序后一一对应。
    """

    def __init__(
        self,
        entries: list[LibraryEntry],
        source_paths: list[str],
        source_sizes: dict[str, int],
    ) -> None:
        self.entries = entries
        self.source_paths = sorted(source_paths)
        self.source_sizes = source_sizes
        self.names = {entry.name for entry in entries}
        # 只用于「库几乎是空的时候」判断文件是不是刚刮进来
        self.largest_size = max((entry.size for entry in entries), default=0)
        self.by_extension: dict[str, list[LibraryEntry]] = {}
        for entry in entries:
            self.by_extension.setdefault(Path(entry.name).suffix.lower(), []).append(entry)
        for bucket in self.by_extension.values():
            bucket.sort(key=lambda item: item.path)
        self._paired: set[str] | None = None

    def paired_paths(self) -> set[str]:
        """源文件里那些「库里已经有对应文件」的路径（按顺序配对）。"""
        if self._paired is not None:
            return self._paired
        paired: set[str] = set()
        buckets = {suffix: list(bucket) for suffix, bucket in self.by_extension.items()}
        for source in self.source_paths:
            bucket = buckets.get(Path(source).suffix.lower())
            if not bucket:
                continue
            paired.add(source)
            bucket.pop(0)
        self._paired = paired
        return paired

    def holds(self, path: Path, size: int) -> bool:
        """库内是否已有对应文件；命中说明自动刮削不该再碰它。

        三道判定，任一命中就算「刮过了」：

        1. 同名文件：``01 - Queen - Bohemian Rhapsody.flac`` 这个名字已经在库里；
        2. 文件名互为后缀：命名模板改过标题（``01 - Bohemian Rhapsody.flac``），
           或反过来带上歌手前缀；
        3. 顺序配对：库里同扩展名的文件数与源目录一致（整批整理完、模板大改），
           这时按排序一一对应即可，不再逐条比名字；
        4. 库几乎是空的时候：库里最大文件比它还大一点点（写标签让文件变大），
           那是刚才刮进去的那个，不是新文件。
        """
        name = path.name.lower()
        if name in self.names:
            return True
        stem = Path(name).stem
        for candidate in self.names:
            other = Path(candidate).stem
            if len(other) < 4 or not stem:
                continue
            if stem.endswith(other) or other.endswith(stem):
                return True
        if str(path) in self.paired_paths():
            return True
        if size <= 0 or self.largest_size <= 0:
            return False
        close = max(256, int(size * 0.02))
        if self.largest_size <= size + close:
            bucket = max(1, len(self.source_paths))
            if len(self.entries) * 2 < bucket:
                return True
        return False


def _same_recording(source: str, entry: LibraryEntry, size: int | None) -> bool:
    """顺序配对时的补充校验，挡住「文件被删了又重新加一个」的误判。"""
    if not size:
        return True
    close = max(1024, int(size * 0.03))
    if entry.size and abs(entry.size - size) <= close:
        return True
    name = Path(source).name.lower()
    other = Path(entry.name).stem.lower()
    stem = Path(name).stem
    return bool(other) and (stem.endswith(other) or other.endswith(stem))


def _index_library(
    library: Path,
    extensions: frozenset[str],
    exclude_dirs: frozenset[str],
) -> list[LibraryEntry]:
    """Walk the music library once; only audio files are indexed."""
    entries: list[LibraryEntry] = []
    for dirpath, dirnames, filenames in os.walk(library, followlinks=False):
        dirnames[:] = [
            name for name in dirnames if name not in exclude_dirs and not name.startswith(".")
        ]
        for filename in filenames:
            if Path(filename).suffix.lower() not in extensions:
                continue
            full = Path(dirpath) / filename
            try:
                stat = full.stat()
            except OSError:
                continue
            entries.append(
                LibraryEntry(
                    path=str(full),
                    name=filename.lower(),
                    size=stat.st_size,
                    mtime=stat.st_mtime,
                )
            )
            if len(entries) >= _LIBRARY_SCAN_LIMIT:
                return entries
    return entries


class AutoScraper:
    """Background loop; one instance per process, owned by the lifespan."""

    def __init__(self, jobs: JobManager, settings: SettingsService) -> None:
        self.jobs = jobs
        self.settings = settings
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._seen: dict[str, tuple[int, float]] = {}
        self._pending: dict[str, tuple[int, float]] = {}
        self._signature: tuple[str, ...] | None = None
        self._last_sync = 0.0
        self._loaded = False
        # 本轮待写库的指纹；攒着批量落盘，别每轮都开一次写入事务
        self._dirty: dict[str, tuple[int, float]] = {}
        self._dirty_prune = False
        # 下一轮扫描只记录存量文件、不排任务（首次开启或刚换了源目录）
        self._baseline_pending = True
        # 音乐库里已有的文件索引，用于判断「这个文件刮过了」
        self._library = LibraryIndex([], [], {})
        self._library_synced = 0.0
        # 刮削完成后登记：这些源文件已经进过库，下一轮不再当新文件
        self._completed: dict[str, tuple[int, float]] = {}
        # 指纹攒到这个数量再落盘，一次事务写完
        self.flush_threshold = 20

        self.running = False
        self.enabled = False
        self.scanning = False
        self.last_scan: float | None = None
        self.last_job_id: str | None = None
        self.last_error = ""
        self.detected = 0
        self.baselined = 0
        self.in_library = 0

    # ---- lifecycle ------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="musicpilot-watcher")

    async def shutdown(self) -> None:
        self.flush()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.running = False

    def kick(self) -> None:
        """Ask the loop to look at the directory right away."""
        self._wake.set()

    def applied(self, runtime: RuntimeSettings) -> None:
        """Reflect a settings change immediately instead of on the next poll."""
        self.enabled = runtime.watch_enabled
        if runtime.watch_enabled:
            # 开启（或又点了一次开启）：存量文件先登记一遍，别一开就全量刮
            self._baseline_pending = True
        self._last_sync = time.time()
        self.kick()

    # ---- introspection --------------------------------------------------
    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "scanning": self.scanning,
            "last_scan": self.last_scan,
            "last_job_id": self.last_job_id,
            "last_job_label": self.jobs.label(self.last_job_id) if self.last_job_id else "",
            "last_error": self.last_error,
            "detected": self.detected,
            "baselined": self.baselined,
            "tracked": len(self._seen),
            "in_library": self.in_library,
        }

    def mark_processed(self, paths: list[str]) -> None:
        """Treat these files as done so the watcher never picks them up again."""
        for value in paths:
            self._remember(value)
        self.flush()

    def release(self, paths: list[str]) -> None:
        """排任务失败时把指纹退回去，下一轮重新判定，别把文件默默丢掉。"""
        for value in paths:
            self._seen.pop(value, None)
            self._dirty.pop(value, None)

    def reset(self) -> None:
        """Forget everything seen: the next scan treats existing files as old ones again."""
        self._seen.clear()
        self._pending.clear()
        self._dirty.clear()
        self._dirty_prune = False
        self._signature = None
        self._baseline_pending = True
        self.baselined = 0
        self.detected = 0
        self._completed.clear()
        self._library = LibraryIndex([], [], {})
        self._library_synced = 0.0
        try:
            self.jobs.store.clear_seen()
        except Exception as exc:  # noqa: BLE001 - resetting the UI must not crash
            self.last_error = f"清空已处理记录失败：{type(exc).__name__}: {exc}"
            logger.warning("清空 watch_seen 失败", exc_info=True)
        self.kick()

    def flush(self) -> None:
        """Persist buffered fingerprints; safe to call from the event loop."""
        if not self._dirty and not self._dirty_prune:
            return
        entries, self._dirty = self._dirty, {}
        prune, self._dirty_prune = self._dirty_prune, False
        try:
            if entries:
                self.jobs.store.save_seen(entries, prune=prune)
            elif prune:
                self.jobs.store.save_seen({}, prune=True)
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            self.last_error = f"记录已处理文件失败：{type(exc).__name__}: {exc}"
            logger.warning("写入 watch_seen 失败", exc_info=True)

    # ---- loop -----------------------------------------------------------
    async def _loop(self) -> None:
        self.running = True
        while True:
            try:
                changed = self._sync_settings()
                if self.enabled:
                    self._load_seen()
                if self.enabled and not changed and self._settle_ready():
                    await self._tick()
                self._maybe_flush(self.flush_threshold)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("自动刮削循环出错")
            await self._sleep(self._interval())

    def _load_seen(self) -> None:
        """Read the persisted fingerprints once per process.

        必须在读设置之后调用：换了源目录时内存指纹已被抹掉，这时把库里的
        记录读回来，才不会把新目录里的存量文件当成新文件刮一遍。
        """
        if self._loaded:
            return
        self._loaded = True
        try:
            self._seen.update(self.jobs.store.load_seen())
        except Exception as exc:  # noqa: BLE001 - fall back to a memory-only run
            self.last_error = f"读取已处理记录失败：{type(exc).__name__}: {exc}"
            logger.warning("读取 watch_seen 失败", exc_info=True)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(1.0, seconds))
        except TimeoutError:
            pass
        finally:
            self._wake.clear()

    def _interval(self) -> float:
        try:
            return self.settings.get().watch_interval
        except SettingsError:
            return 30.0

    def _settle_ready(self) -> bool:
        """Wait ``watch_settle`` seconds after a start/toggle switch before scanning."""
        now = time.time()
        if self._last_sync == 0.0:
            self._last_sync = now
            return False
        try:
            return now - self._last_sync >= self.settings.get().watch_settle
        except SettingsError:
            return False

    def _sync_settings(self) -> bool:
        """Mirror settings into the loop; True when the source root just changed."""
        runtime = self.settings.get()
        signature = (
            str(runtime.source_root),
            str(runtime.library_root),
            "watch" if runtime.watch_enabled else "off",
        )
        self.enabled = runtime.watch_enabled
        if self._signature == signature:
            return False
        previous, self._signature = self._signature, signature
        self._last_sync = time.time()
        if previous is None:
            return False
        # 换源目录或重新开启监控：抹掉内存指纹，下一轮把存量文件当旧文件记下来
        self._seen.clear()
        self._pending.clear()
        self._baseline_pending = True
        self.baselined = 0
        self.kick()
        return True

    async def _tick(self) -> None:
        if self.jobs.is_busy() or self.scanning:
            return
        self.scanning = True
        try:
            runtime = self.settings.get()
            root = runtime.source_root
            if not root.is_dir():
                self.last_error = f"源目录不存在：{root}"
                return
            found, _warnings = await asyncio.to_thread(
                scan_files,
                root,
                extensions=self.jobs.scraper.base.extensions,
                exclude_dirs=self.jobs.scraper.base.excluded_directories,
                max_files=self.jobs.scraper.base.max_scan_files,
            )
            fresh: list[Path] = []
            baselining = self._baseline_pending
            if not found:
                self._baseline_pending = False
            candidates = [
                audio
                for audio in found
                if not self._partial(audio) and not self._unstable(audio)
            ]
            sizes = {str(audio): self._size_of(audio) for audio in candidates}
            await self._sync_library(candidates, sizes)
            for audio in candidates:
                path = str(audio)
                fingerprint = self._fingerprint(path)
                if fingerprint is None:
                    continue
                if baselining:
                    # 刚开启 / 刚换目录：存量文件只记不刮，老文件不碰
                    self._remember(path, fingerprint)
                    self.baselined += 1
                    continue
                if self._in_library(audio, fingerprint):
                    # 库里已经有这个文件：算处理过，自动刮削不再碰它
                    self._pending.pop(path, None)
                    if self._seen.get(path) != fingerprint:
                        self._remember(path, fingerprint)
                    continue
                if path in self._completed:
                    # 本次进程里刚刮完：写标签改了 mtime，别再当新文件
                    self._pending.pop(path, None)
                    self._remember(path, fingerprint)
                    continue
                if self._seen.get(path) == fingerprint:
                    self._pending.pop(path, None)
                    continue
                if self._pending.get(path) != fingerprint:
                    # 大小或修改时间还在变：下一轮再确认一次，别抢着刮半成品
                    self._pending[path] = fingerprint
                    continue
                self._pending.pop(path, None)
                fresh.append(audio)
            self._baseline_pending = False
            self.last_scan = time.time()
            self.last_error = ""
            if not fresh:
                self._maybe_flush()
                return
            batch = sorted(fresh)[:MAX_BATCH]
            paths = [str(path) for path in batch]
            self.mark_processed(paths)
            try:
                job = self.jobs.start(
                    JobRequest(kind=JobKind.AUTO, paths=paths),
                    title=batch[0].name if len(batch) == 1 else "",
                )
            except BaseException:
                self.release(paths)
                raise
            self.last_job_id = job.id
            self.detected += len(batch)
            for key in paths:
                self._completed[key] = self._fingerprint(key) or (0, 0.0)
            logger.info("自动刮削：已排入 %s 个新文件，任务 %s", len(batch), job.id)
            self._maybe_flush()
        except JobConflict:
            return  # 手动任务在跑，下一轮再看
        except SettingsError as exc:
            self.last_error = str(exc)
        finally:
            self.scanning = False

    async def _sync_library(
        self,
        source_files: list[Path] | None = None,
        source_sizes: dict[str, int] | None = None,
    ) -> None:
        """Index the music library so "already scraped" survives restarts.

        只看音乐库、不看时间戳：写标签会改动源文件的 mtime，而复制 / 硬链接 /
        软链接模式下源文件又一直留在原地，指纹永远对不上它已经进过库这件事。
        """
        runtime = self.settings.get()
        library = runtime.library_root
        if not library.is_dir():
            self._library = LibraryIndex([], [], {})
            self.in_library = 0
            return
        interval = max(30.0, runtime.watch_interval)
        if (
            self._library_synced
            and time.time() - self._library_synced < interval
            and source_files is None
        ):
            return
        try:
            entries = await asyncio.to_thread(
                _index_library,
                library,
                self.jobs.scraper.base.extensions,
                self.jobs.scraper.base.excluded_directories,
            )
        except OSError as exc:
            self.last_error = f"读取音乐库失败：{type(exc).__name__}: {exc}"
            return
        self._library = LibraryIndex(
            entries,
            [str(path) for path in (source_files or [])],
            source_sizes or {},
        )
        self._library_synced = time.time()
        self.in_library = len(entries)

    def _in_library(self, path: Path, fingerprint: tuple[int, float]) -> bool:
        """True when the library already holds this recording."""
        if path.name.lower() in self._library.names:
            return True
        paired = self._library.paired_paths()
        if str(path) in paired:
            return True
        return self._library.holds(path, fingerprint[0])

    def _maybe_flush(self, threshold: int = 50) -> None:
        """Write buffered fingerprints once they are worth a transaction."""
        if len(self._dirty) >= threshold or (self._dirty and self._dirty_prune):
            self.flush()

    @staticmethod
    def _partial(path: Path) -> bool:
        """Downloader leftovers: the real file may still be on its way."""
        name = path.name.lower()
        return any(name.endswith(suffix) for suffix in _PARTIAL_SUFFIXES)

    @staticmethod
    def _size_of(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _fingerprint(path: str) -> tuple[int, float] | None:
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        return stat.st_size, round(stat.st_mtime, 3)

    def _unstable(self, path: Path) -> bool:
        """A file still being written/downloaded gets another chance next round."""
        try:
            stat = path.stat()
        except OSError:
            return True
        settle = self.settings.get().watch_settle
        return settle > 0 and time.time() - stat.st_mtime < settle

    def _remember(self, path: str, fingerprint: tuple[int, float] | None = None) -> None:
        value = fingerprint or self._fingerprint(path) or (0, 0.0)
        self._seen[path] = value
        self._dirty[path] = value
        if len(self._seen) > _MAX_SEEN:
            for key in list(self._seen)[: _MAX_SEEN // 10]:
                self._seen.pop(key, None)
            self._dirty_prune = True
