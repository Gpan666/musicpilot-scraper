"""The four ways a scraped file can enter the music library.

This module is deliberately self-contained and side-effect free apart from the
explicit :func:`transfer` call, so the rules can be unit-tested on any platform.

IMPORTANT ORDERING CONTRACT
---------------------------
Tag writing happens *before* the transfer. For ``copy``/``move`` the source is
still an independent file at that point. For ``hardlink``/``symlink`` the target
shares the source data (same inode or a pointer to it), so writing tags to the
source also completes the tags of the target -- and any later edit to either
side affects the other. See MODE_INFO for the user-facing explanation.
"""

from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class TransferMode(StrEnum):
    COPY = "copy"
    MOVE = "move"
    HARDLINK = "hardlink"
    SYMLINK = "symlink"


class ConflictPolicy(StrEnum):
    SKIP = "skip"
    OVERWRITE = "overwrite"
    RENAME = "rename"


# 失败原因按异常类型翻译成中文，异常原文只作补充，避免整句英文。
TYPE_LABELS = {
    "FileExistsError": "目标已存在",
    "FileNotFoundError": "文件或目录不存在",
    "PermissionError": "权限不足",
    "IsADirectoryError": "目标是目录",
    "NotADirectoryError": "路径中有非目录项",
    "OSError": "文件操作失败",
}
MODE_LABELS: dict[str, str] = {
    TransferMode.COPY.value: "复制",
    TransferMode.MOVE.value: "移动",
    TransferMode.HARDLINK.value: "硬链接",
    TransferMode.SYMLINK.value: "软链接",
}


def mode_label(mode: str) -> str:
    """Chinese label for a stored transfer mode (``copy`` -> ``复制``)."""
    return MODE_LABELS.get(str(mode or "").strip(), "未知方式")


MODE_INFO: dict[TransferMode, dict[str, Any]] = {
    TransferMode.COPY: {
        "mode": "copy",
        "label": "复制",
        "short": "复制一份到音乐库，源文件保持不动。",
        "how": "整份复制文件内容并保留时间戳，先写同目录临时文件，再原子替换为目标文件。",
        "pros": [
            "源文件完全不受影响，也不共用同一份数据",
            "可跨文件系统、跨磁盘、跨容器挂载点",
            "目标文件独立，之后重新刮削标签只会改目标文件",
        ],
        "cons": ["占用双份磁盘空间", "大文件需要完整复制，耗时与文件体积成正比"],
        "space": "额外占用一份完整文件大小",
        "source_after": "保留，与刮削前后一致（除标签被补全）",
        "cross_filesystem": True,
        "survives_source_delete": True,
        "tag_effect": "写标签作用于源文件，目标随后被复制，两者内容相同但互不影响",
        "use_when": "默认与最安全的选项；源/目标在不同磁盘或不同挂载点；源文件还要继续做种或保留备份",
        "fails_when": "磁盘空间不足、目标目录不可写、源文件正被别的程序占用",
    },
    TransferMode.MOVE: {
        "mode": "move",
        "label": "移动",
        "short": "把源文件搬进音乐库，源目录不再保留该文件。",
        "how": "同一文件系统内直接改目录项，不复制数据；跨文件系统时退化为「复制到临时文件 → 原子替换 → 删除源文件」。",
        "pros": ["同设备内瞬间完成且不额外占用空间", "源目录被清空，不留重复文件"],
        "cons": [
            "源文件不再存在，做种/分享/回滚场景不适用",
            "跨设备时不是单次原子操作，中断可能留下临时文件",
            "正在被下载器或播放器占用的文件可能移动失败",
        ],
        "space": "同设备不额外占用；跨设备短暂占用一份",
        "source_after": "源文件被移动到目标位置",
        "cross_filesystem": True,
        "survives_source_delete": True,
        "tag_effect": "写标签先作用于源文件，移动后标签随文件一起进入音乐库",
        "use_when": "把临时下载目录当作暂存区，希望整理后自动清空",
        "fails_when": "源文件被占用或只读、跨设备复制失败（此时源文件不会丢）",
    },
    TransferMode.HARDLINK: {
        "mode": "hardlink",
        "label": "硬链接",
        "short": "在音乐库建立指向同一份数据的另一个目录项，零额外空间。",
        "how": "在目标路径上为同一份文件数据再挂一个目录项；源和目标在数据层面是同一个文件，只是有两个名字。",
        "pros": [
            "不额外占用磁盘空间",
            "删除任一边的名字，另一边仍可正常读取全部内容",
            "内容永远同步，无需重复复制",
        ],
        "cons": [
            "必须同一文件系统且同一挂载点：分别挂载的两个卷即使在同一块物理硬盘上也会失败",
            "Btrfs 子卷、不同分区、FAT/exFAT 与多数网络文件系统不支持",
            "两边共用同一份数据，之后任一边被修改（重新写标签、编辑器覆盖、下载器续传）另一边同时变化",
            "部分网盘同步/备份工具不识别硬链接，可能把两份都上传",
        ],
        "space": "几乎为零（只多一个目录项）",
        "source_after": "保留，且与目标是同一份数据",
        "cross_filesystem": False,
        "survives_source_delete": True,
        "tag_effect": "标签在建立链接前写入源文件，因此两边都完整；此后修改任一边会同时影响另一边",
        "use_when": "源目录与音乐库在同一挂载点、希望零空间开销、且源文件需要保留做种",
        "fails_when": "源与库不在同一个挂载点（不同设备）、文件系统不支持硬链接、目标目录不可写",
    },
    TransferMode.SYMLINK: {
        "mode": "symlink",
        "label": "软链接",
        "short": "在音乐库创建一个指向源文件路径的快捷方式。",
        "how": "写入一个文本指针；默认写相对路径，只要源/目标目录的相对结构不变，容器内创建的链接在宿主机上也能解析。",
        "pros": ["不占用额外空间、创建最快", "可以跨文件系统、跨磁盘、跨挂载点"],
        "cons": [
            "源文件被移动、改名或删除后链接立即失效（悬空链接）",
            "播放/扫描端必须能访问到源路径；媒体服务器容器内若未挂载源目录会读不到",
            "部分媒体库扫描器默认不跟随符号链接",
            "Windows 上创建软链接通常需要管理员权限或开发者模式",
        ],
        "space": "几乎为零（只存一个路径字符串）",
        "source_after": "保留，但成为链接唯一依赖的目标",
        "cross_filesystem": True,
        "survives_source_delete": False,
        "tag_effect": "链接本身没有数据，写标签实际上改的是源文件；链接失效后音乐库里只剩一个坏入口",
        "use_when": "只想给现有文件建立另一种目录视图、或源与库无法建立硬链接时",
        "fails_when": "权限不足、文件系统不支持软链接、源路径对读取端不可见",
    },
}


def describe_modes() -> list[dict[str, Any]]:
    """Machine-readable documentation used by the Web UI and ``/api/transfer-modes``."""
    return [MODE_INFO[mode] for mode in TransferMode]


@dataclass(frozen=True, slots=True)
class TransferResult:
    mode: TransferMode
    source: str
    target: str | None
    status: str  # transferred | skipped | failed
    detail: str
    link_target: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "detail": self.detail,
            "link_target": self.link_target,
        }


@dataclass(frozen=True, slots=True)
class Capability:
    mode: TransferMode
    available: bool | None
    reason: str
    same_device: bool | None = None
    probed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "label": MODE_INFO[self.mode]["label"],
            "available": self.available,
            "reason": self.reason,
            "same_device": self.same_device,
            "probed": self.probed,
        }


def device_of(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def inspect_modes(
    source_root: Path,
    library_root: Path,
    *,
    probe: bool = False,
) -> list[Capability]:
    """Report whether each transfer mode can work with the configured directories.

    ``probe=False`` only reads ``st_dev`` and directory existence. ``probe=True``
    additionally creates (and removes) one throwaway file/link per mode in the
    target directory, which detects permission and filesystem limitations.
    """
    source_device = device_of(source_root)
    library_device = device_of(library_root)
    same_device = (
        None if source_device is None or library_device is None
        else source_device == library_device
    )
    source_exists = source_root.is_dir()
    library_exists = library_root.is_dir()

    probe_results: dict[TransferMode, tuple[bool, str]] = {}
    if probe and source_exists and library_exists:
        probe_results = _probe_writes(source_root, library_root)

    capabilities: list[Capability] = []
    for mode in TransferMode:
        available: bool | None = None
        reason = "目录尚未就绪，无法判断"
        probed = mode in probe_results
        if not source_exists or not library_exists:
            reason = "源目录或音乐库目录不存在（首次使用前请先创建）"
            if probe:
                available = False
        elif mode is TransferMode.HARDLINK:
            if same_device is False:
                available = False
                reason = (
                    "源目录与音乐库不在同一挂载点（设备号不同），硬链接会失败；"
                    "请把两者放到同一个挂载点下，或改用复制/移动/软链接"
                )
            elif probed:
                available, reason = probe_results[mode]
            else:
                available = same_device
                reason = "源目录与音乐库在同一文件系统/挂载点，可建立硬链接"
        elif probed:
            available, reason = probe_results[mode]
        else:
            reason = "基础检查通过；如需确认写权限与链接支持，请运行一次写入自检"

        capabilities.append(
            Capability(
                mode=mode,
                available=available,
                reason=reason,
                same_device=same_device,
                probed=probed,
            )
        )
    return capabilities


def _probe_writes(
    source_root: Path,
    library_root: Path,
) -> dict[TransferMode, tuple[bool, str]]:
    """Create and immediately remove one throwaway artefact per mode."""
    token = uuid.uuid4().hex[:10]
    stem = f".musicpilot-probe-{token}"
    source_file = source_root / f"{stem}.src"
    target_file = library_root / f"{stem}.dst"
    results: dict[TransferMode, tuple[bool, str]] = {}
    try:
        source_file.write_bytes(b"musicpilot-probe")
    except OSError as exc:
        reason = f"源目录不可写：{exc}"
        return {mode: (False, reason) for mode in TransferMode}

    try:
        results[TransferMode.HARDLINK] = _try_probe(
            lambda: os.link(source_file, target_file)
        )
        _remove_probe(target_file)

        relative = os.path.relpath(source_file, target_file.parent)
        results[TransferMode.SYMLINK] = _try_probe(
            lambda: os.symlink(relative, target_file)
        )
        _remove_probe(target_file)

        results[TransferMode.COPY] = _try_probe(lambda: shutil.copy2(source_file, target_file))
        _remove_probe(target_file)

        results[TransferMode.MOVE] = (True, "读写检查通过，移动可执行")
    finally:
        _remove_probe(source_file)
        _remove_probe(target_file)
    return results


def _try_probe(action: Any) -> tuple[bool, str]:
    try:
        action()
    except OSError as exc:
        return False, f"自检失败：{exc}"
    return True, "自检通过，可正常创建"


def _remove_probe(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
    except OSError:
        pass


def _temp_target(target: Path) -> Path:
    return target.with_name(f".{target.name}.musicpilot-{uuid.uuid4().hex[:8]}.tmp")


def _cleanup_temp(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def resolve_conflict(target: Path, policy: ConflictPolicy) -> tuple[Path | None, str]:
    """Return the final destination, or ``None`` when the item must be skipped."""
    if not (target.exists() or target.is_symlink()):
        return target, ""
    if policy is ConflictPolicy.SKIP:
        return None, "目标已存在，已跳过"
    if policy is ConflictPolicy.OVERWRITE:
        return target, "目标已存在，已覆盖"
    stem, suffix = target.stem, target.suffix
    for index in range(2, 1000):
        candidate = target.with_name(f"{stem} ({index}){suffix}")
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate, "目标已存在，已自动改名"
    return None, "目标冲突且无法改名，已跳过"


def same_file(source: Path, target: Path) -> bool:
    try:
        return source.samefile(target)
    except OSError:
        return False


def transfer(
    source: Path,
    target: Path,
    *,
    mode: TransferMode,
    conflict_policy: ConflictPolicy = ConflictPolicy.RENAME,
    symlink_relative: bool = True,
    allowed_root: Path | None = None,
) -> TransferResult:
    """Move a scraped source file into the library using ``mode``.

    Never raises for expected filesystem failures: they come back as
    ``status="failed"`` with an actionable message so a batch job can continue.
    """
    def result(
        status: str,
        detail: str,
        destination: Path | None = None,
        link_target: str | None = None,
    ) -> TransferResult:
        return TransferResult(
            mode=mode,
            source=str(source),
            target=str(destination) if destination is not None else None,
            status=status,
            detail=detail,
            link_target=link_target,
        )

    if not source.is_file():
        return result("failed", "源文件不存在或不是普通文件")
    if allowed_root is not None:
        try:
            if not target.resolve().is_relative_to(allowed_root.resolve()):
                return result("failed", "目标路径越出音乐库目录，已拒绝")
        except OSError:
            return result("failed", "无法解析目标路径")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return result("failed", "无法创建目标目录，请检查权限")

    if same_file(source, target):
        return result("skipped", "源与目标已是同一个文件", destination=target)

    destination, conflict_note = resolve_conflict(target, conflict_policy)
    if destination is None:
        return result("skipped", conflict_note)

    if conflict_policy is ConflictPolicy.OVERWRITE and conflict_note:
        _remove_probe(destination)

    temporary = _temp_target(destination)
    link_target: str | None = None
    try:
        if mode is TransferMode.COPY:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            detail = "已复制进库，源文件保留"

        elif mode is TransferMode.MOVE:
            source_device = device_of(source.parent)
            target_device = device_of(destination.parent)
            if source_device is not None and source_device == target_device:
                os.replace(source, destination)
                detail = "已移动进库，源文件已删除"
            else:
                shutil.copy2(source, temporary)
                os.replace(temporary, destination)
                source.unlink()
                detail = "跨磁盘：已复制进库并删除源文件"

        elif mode is TransferMode.HARDLINK:
            source_device = device_of(source)
            target_device = device_of(destination.parent)
            if source_device is None or target_device is None or source_device != target_device:
                return result(
                    "failed",
                    "硬链接要求源与库在同一挂载点，请改用复制、移动或软链接",
                )
            os.link(source, temporary)
            os.replace(temporary, destination)
            detail = "已建硬链接，与源文件共用同一份数据"

        elif mode is TransferMode.SYMLINK:
            if symlink_relative:
                link_target = os.path.relpath(source, destination.parent)
            else:
                link_target = str(source.resolve())
            os.symlink(link_target, temporary)
            os.replace(temporary, destination)
            detail = "已建软链接，指向源文件"
        else:  # pragma: no cover - enum is exhaustive
            return result("failed", f"不支持的转移方式：{mode}")

    except OSError as exc:
        _cleanup_temp(temporary)
        hint = ""
        if mode is TransferMode.SYMLINK and os.name == "nt":
            hint = "（Windows 建软链接需要管理员权限或开发者模式）"
        return result("failed", f"{TYPE_LABELS.get(type(exc).__name__, '文件操作出错')}：{exc}{hint}")

    _cleanup_temp(temporary)
    if conflict_note:
        detail = f"{conflict_note}；{detail}"
    return result("transferred", detail, destination=destination, link_target=link_target)
