"""Read-only scanning of the source directory."""

from __future__ import annotations

import base64
import os
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from mutagen.flac import Picture

from musicpilot.domain.library import classify_kind
from musicpilot.domain.matcher import merge_metadata, parse_filename
from musicpilot.domain.models import AudioFile, TrackMetadata

_TAG_KEYS: dict[str, tuple[str, ...]] = {
    "title": ("title", "TIT2", "\xa9nam"),
    "artist": ("artist", "TPE1", "\xa9ART"),
    "album": ("album", "TALB", "\xa9alb"),
    "album_artist": ("albumartist", "album artist", "TPE2", "aART"),
    "date": ("date", "year", "TDRC", "TYER", "\xa9day"),
    "tracknumber": ("tracknumber", "track", "TRCK", "trkn"),
    "discnumber": ("discnumber", "disc", "TPOS", "disk"),
    # 专辑类型：各家写在不同键里，含 single 就按单曲处理
    "albumtype": ("albumtype", "releasetype", "musicbrainz_albumtype", "TMED", "stik"),
    # ID3 的歌词帧在 mutagen 里叫 ``USLT::<语言>``（如 USLT::chi），只写 "USLT" 永远取不到。
    "lyrics": ("lyrics", "unsyncedlyrics", "USLT", "\xa9lyr"),
}
# 封面键名各格式不同：mp3/ID3 是 ``APIC:<描述>``（如 ``APIC:`` / ``APIC:cover``），
# MP4 是 ``covr``，Vorbis/FLAC 是 ``metadata_block_picture``（base64 的 Picture）。
_COVER_KEYS = ("APIC", "covr", "metadata_block_picture")


def _safe_get(audio: Any, key: str) -> Any:
    """Read a raw tag value, tolerating mutagen's inconsistent backends.

    mutagen's Vorbis-family tags (FLAC/Ogg/Opus) raise ``ValueError`` on a
    *missing* key instead of returning ``None`` like the MP3/ID3 backend does.
    Any lookup failure is treated as "no value" so a scan never crashes.
    """
    try:
        return audio.get(key)
    except (KeyError, ValueError, AttributeError, TypeError):
        return None


def _first(audio: Any, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _safe_get(audio, key)
        if value is None:
            # 前缀匹配：ID3 歌词帧是 ``USLT::chi`` 这类带语言后缀的键
            value = _prefixed(audio, key)
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        for item in values:
            raw = getattr(item, "text", item)
            if isinstance(raw, (list, tuple)):
                raw = raw[0] if raw else ""
            text = str(raw).strip()
            if text and not text.startswith("("):
                return text
    return None


def _prefixed(audio: Any, key: str, sep: str = "::", *, allow_bare: bool = False) -> Any:
    """Find a tag whose key is ``key + sep + suffix`` (e.g. ``USLT::chi``).

    ``allow_bare`` also accepts the key itself, for mutagen's ``APIC:`` frame
    with an empty description: it is stored as ``"APIC:"``, which the exact
    lookup of ``"APIC"`` misses.
    """
    tags = getattr(audio, "tags", None)
    keys = getattr(tags, "keys", None)
    if not callable(keys):
        return None
    try:
        candidates = list(keys())
        for candidate in candidates:
            name = str(candidate)
            if name == key and not allow_bare:
                continue
            if name == key or name.startswith(key + sep):
                return tags[candidate]
    except (KeyError, ValueError, TypeError):
        return None
    return None


def _cover_entry(audio: Any) -> Any:
    """Return the first embedded cover *entry*, or ``None``.

    The entry is whatever the backend stores: an ID3 ``APIC`` frame, an MP4
    ``MP4Cover``, a base64 ``metadata_block_picture`` string (Vorbis), or a
    ``Picture`` from FLAC's ``audio.pictures`` list — which is an **attribute**,
    not a tag key, so it needs its own branch. This is the single source of
    truth for both "has cover" (``read_tags``) and "give me the bytes"
    (``embedded_cover``), so the two can never disagree.

    Only looks at tag metadata, so checking "has cover" costs no more than
    reading the tags that were already read.
    """
    pictures = getattr(audio, "pictures", None)  # FLAC：属性，不在 tags 里
    if pictures:
        return pictures[0]

    for key in _COVER_KEYS:
        value = _safe_get(audio, key)
        if value is None and key == "APIC":
            # 只有 ID3 的封面帧带描述后缀（``APIC:`` / ``APIC:cover``）；
            # ``covr`` / ``metadata_block_picture`` 没有这种键，不做前缀匹配
            value = _prefixed(audio, key, ":", allow_bare=True)
        if value is None:
            continue
        values = value if isinstance(value, (list, tuple)) else [value]
        if values:
            return values[0]
    return None


def _picture_bytes(entry: Any) -> tuple[bytes, str]:
    """Turn a cover entry into ``(bytes, mime)``; empty bytes mean "no usable picture"."""
    if isinstance(entry, str):
        # Vorbis 的 metadata_block_picture 是 base64 过的 FLAC Picture 块
        try:
            return _picture_bytes(Picture(base64.b64decode(entry)))
        except Exception:  # noqa: BLE001 - 坏封面块当成没封面，扫描不能崩
            return b"", ""
    data = getattr(entry, "data", entry)
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return b"", ""
    data = bytes(data)
    if not data:
        return b"", ""
    return data, _mime_of(entry)


def _int_value(text: str | None, *, limit: int = 999) -> int | None:
    if not text:
        return None
    head = text.split("/", 1)[0].strip()
    if head.startswith("("):
        head = head[1:]
    try:
        value = int(head)
    except ValueError:
        return None
    return value if 0 < value <= limit else None


def read_tags(path: Path) -> tuple[TrackMetadata, bool, dict[str, Any]]:
    """Return (existing tags, has embedded cover, technical info)."""
    empty = TrackMetadata()
    try:
        audio = MutagenFile(str(path))
    except Exception:  # noqa: BLE001 - unreadable/corrupt files must not break a scan
        return empty, False, {}
    if audio is None:
        return empty, False, {}

    def tag(field: str) -> str | None:
        return _first(audio, _TAG_KEYS[field])

    date_text = tag("date")
    year = _int_value(date_text[:4] if date_text else None, limit=9999)
    metadata = TrackMetadata(
        title=tag("title") or "",
        artist=tag("artist"),
        album=tag("album"),
        album_artist=tag("album_artist"),
        year=year,
        track_number=_int_value(tag("tracknumber")),
        disc_number=_int_value(tag("discnumber")),
        # 只用来判断「文件里有没有歌词」，内容本身不进对比（太长，界面只看有没有）
        lyrics=tag("lyrics"),
        source="tags",
        external_id=str(path),
    )
    # 判「有没有封面」与取封面（embedded_cover）同一个入口：能取出字节才算有，
    # 这样两边口径不会分叉（取不出字节的封面在音乐库页也只是占位图）。
    entry = _cover_entry(audio)
    has_cover = bool(entry is not None and _picture_bytes(entry)[0])
    info = getattr(audio, "info", None)
    technical = {
        "duration": float(getattr(info, "length", 0) or 0) or None,
        "bitrate": int(getattr(info, "bitrate", 0) or 0) or None,
        # 只用于判定单曲 / 专辑，不做展示，所以跟着「顺手读到的技术信息」一起回去
        "album_type": tag("albumtype") or "",
    }
    close = getattr(audio, "close", None)
    if callable(close):
        close()
    return metadata, bool(has_cover), technical


def probe_file(
    path: Path,
    *,
    root: Path | None = None,
    sibling_audio_count: int | None = None,
) -> AudioFile:
    stat = path.stat()
    tagged, has_cover, technical = read_tags(path)
    guessed = parse_filename(path, root=root)
    # Existing tags win; filename/directory hints only fill the blanks.
    local = merge_metadata(tagged, guessed)
    local = with_kind(local, sibling_audio_count, technical.get("album_type"))
    return AudioFile(
        path=str(path),
        size=stat.st_size,
        modified_at=stat.st_mtime,
        extension=path.suffix.lower(),
        duration=technical.get("duration"),
        bitrate=technical.get("bitrate"),
        local_metadata=local,
        has_embedded_cover=has_cover,
    )


def with_kind(
    meta: TrackMetadata,
    sibling_audio_count: int | None,
    album_type: str | None,
) -> TrackMetadata:
    """给本地元数据挂上 ``kind`` / ``kind_reason``；判定依据由调用方一次统计好。"""
    kind, reason = classify_kind(
        meta, sibling_audio_count=sibling_audio_count, album_type=album_type
    )
    return replace(meta, kind=kind, kind_reason=reason)


def audio_counts(paths: Iterable[Path]) -> dict[Path, int]:
    """一次遍历统计「每个目录里有多少音频文件」。

    154 首曲目只该数一遍目录，不能每个文件都去 listdir 一次。传入的路径列表本来
    就是 walk 出来的结果，这里只是顺手归并，不多碰一次磁盘。
    """
    counts: dict[Path, int] = {}
    for path in paths:
        parent = path.parent
        counts[parent] = counts.get(parent, 0) + 1
    return counts


def audio_in_dir(directory: Path, *, extensions: frozenset[str]) -> int:
    """单曲判定要用的「本目录音频文件数」；只为单个文件查一次，不整库遍历。"""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0
    return sum(
        1
        for entry in entries
        if entry.suffix.lower() in extensions and not entry.is_dir()
    )


def embedded_cover(path: Path) -> tuple[bytes, str] | None:
    """Extract the first embedded picture as decoded ``(bytes, mime)``."""
    try:
        audio = MutagenFile(str(path))
    except Exception:  # noqa: BLE001 - corrupt files must not break the UI
        return None
    if audio is None:
        return None

    # 与 read_tags 判封面共用 _cover_entry：FLAC 的 ``pictures`` 是属性，
    # mp3 的 ``APIC:<描述>``、MP4 的 ``covr`` 和 Vorbis 的 ``metadata_block_picture`` 是标签。
    entry = _cover_entry(audio)
    if entry is None:
        return None
    data, mime = _picture_bytes(entry)
    return (data, mime) if data else None


def _mime_of(frame: Any) -> str:
    mime = str(getattr(frame, "mime", "") or getattr(frame, "imageformat", "") or "")
    mime = mime.split(";")[0].strip().lower()
    return mime if mime.startswith("image/") else "image/jpeg"


def scan_files(
    root: Path,
    *,
    extensions: frozenset[str],
    exclude_dirs: frozenset[str],
    max_files: int = 5000,
) -> tuple[list[Path], list[str]]:
    """Walk ``root`` without following symlinked directories; return (files, warnings)."""
    if not root.is_dir():
        return [], [f"源目录不存在：{root}"]
    found: list[Path] = []
    warnings: list[str] = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept = []
        for name in dirnames:
            if name in exclude_dirs or name.startswith("."):
                continue
            if (current / name).is_symlink():
                continue
            kept.append(name)
        dirnames[:] = kept
        for filename in filenames:
            candidate = current / filename
            if candidate.suffix.lower() not in extensions:
                continue
            if len(found) >= max_files:
                truncated = True
                break
            try:
                if candidate.is_file():
                    found.append(candidate)
            except OSError:
                continue
        if truncated:
            break
    if truncated:
        warnings.append(f"文件数量达到上限 {max_files}，本次只处理前 {max_files} 个文件")
    return sorted(found), warnings


def scan(
    root: Path,
    *,
    extensions: frozenset[str],
    exclude_dirs: frozenset[str],
    max_files: int = 5000,
) -> tuple[list[AudioFile], list[str]]:
    paths, warnings = scan_files(
        root, extensions=extensions, exclude_dirs=exclude_dirs, max_files=max_files
    )
    # 同目录音频数在这里算一次，后面每个文件都用同一份结果判单曲 / 专辑
    counts = audio_counts(paths)
    files: list[AudioFile] = []
    for path in paths:
        try:
            files.append(
                probe_file(path, root=root, sibling_audio_count=counts.get(path.parent))
            )
        except OSError as exc:
            warnings.append(f"无法读取 {path.name}：{exc}")
    return files, warnings
