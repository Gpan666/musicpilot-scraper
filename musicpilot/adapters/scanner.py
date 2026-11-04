"""Read-only scanning of the source directory."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

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
}
_COVER_KEYS = ("APIC", "covr", "metadata_block_picture", "pictures")


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
        source="tags",
        external_id=str(path),
    )
    has_cover = any(
        _safe_get(audio, key) or getattr(audio, "pictures", None)
        for key in _COVER_KEYS
    )
    info = getattr(audio, "info", None)
    technical = {
        "duration": float(getattr(info, "length", 0) or 0) or None,
        "bitrate": int(getattr(info, "bitrate", 0) or 0) or None,
    }
    close = getattr(audio, "close", None)
    if callable(close):
        close()
    return metadata, bool(has_cover), technical


def probe_file(path: Path, *, root: Path | None = None) -> AudioFile:
    stat = path.stat()
    tagged, has_cover, technical = read_tags(path)
    guessed = parse_filename(path, root=root)
    # Existing tags win; filename/directory hints only fill the blanks.
    local = merge_metadata(tagged, guessed)
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
    files: list[AudioFile] = []
    for path in paths:
        try:
            files.append(probe_file(path, root=root))
        except OSError as exc:
            warnings.append(f"无法读取 {path.name}：{exc}")
    return files, warnings
