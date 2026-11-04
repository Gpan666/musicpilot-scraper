"""Naming templates and the path-safety rules that keep every target inside the library."""

from __future__ import annotations

import re
from pathlib import Path
from string import Formatter
from typing import Any

from musicpilot.domain.models import TrackMetadata

TEMPLATE_FIELDS = frozenset(
    {
        "artist",
        "album",
        "album_artist",
        "title",
        "track",
        "disc",
        "year",
        "ext",
        "filename",
    }
)

DEFAULT_TEMPLATE = "{artist}/{album}/{track:02d} - {title}"
TEMPLATE_PRESETS = (
    {"name": "歌手 / 专辑 / 音轨", "template": DEFAULT_TEMPLATE},
    {"name": "歌手 / 专辑(年份) / 音轨", "template": "{artist}/{album} ({year})/{track:02d} - {title}"},
    {"name": "歌手 / 单曲", "template": "{artist}/{artist} - {title}"},
    {"name": "扁平目录", "template": "{artist} - {title}"},
    {"name": "保留原文件名", "template": "{artist}/{album}/{filename}"},
)

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?$", re.IGNORECASE
)
_MAX_COMPONENT_LENGTH = 120


class _Value:
    """A template value that tolerates numeric format specs even when missing."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __format__(self, spec: str) -> str:
        text = str(self.value)
        if not spec:
            return text
        try:
            return format(self.value, spec)
        except (TypeError, ValueError):
            return text


class _SafeFormatter(Formatter):
    def get_field(self, field_name: str, args: Any, kwargs: Any) -> Any:
        if "." in field_name or "[" in field_name:
            raise ValueError(f"模板不允许访问属性或索引：{field_name}")
        if field_name not in TEMPLATE_FIELDS:
            raise ValueError(f"未知的模板变量：{{{field_name}}}")
        if field_name not in kwargs:
            raise ValueError(f"缺少模板变量：{{{field_name}}}")
        return kwargs[field_name], field_name


def validate_template(template: str) -> None:
    """Raise ``ValueError`` for unknown fields, attribute access or bad syntax."""
    if not template.strip():
        raise ValueError("命名模板不能为空")
    if not isinstance(template, str) or len(template) > 300:
        raise ValueError("命名模板长度不能超过 300 字符")
    formatter = _SafeFormatter()
    for _, field_name, format_spec, _ in formatter.parse(template):
        if field_name is None:
            continue
        if field_name not in TEMPLATE_FIELDS:
            raise ValueError(f"未知的模板变量：{{{field_name}}}")
        if format_spec and not re.fullmatch(r"[0-9<>^+\- #,._d\s]{0,16}", format_spec):
            raise ValueError(f"不支持的格式化选项：{format_spec}")
    # Force a full parse with dummy values to catch malformed braces / conversion flags.
    values = {name: _Value("x") for name in TEMPLATE_FIELDS}
    try:
        formatter.vformat(template, (), values)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - rethrow as an actionable validation error
        raise ValueError(f"模板语法错误：{exc}") from exc


def sanitize_component(value: str, unknown: str = "Unknown") -> str:
    cleaned = _ILLEGAL_CHARS.sub("_", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    cleaned = cleaned.strip()
    if not cleaned:
        return unknown
    if _WINDOWS_RESERVED.match(cleaned):
        cleaned = f"_{cleaned}"
    return cleaned[:_MAX_COMPONENT_LENGTH].rstrip(" .") or unknown


def _template_values(meta: TrackMetadata, extension: str, unknown: str) -> dict[str, _Value]:
    def text(value: str | None, fallback: str | None = None) -> str:
        return sanitize_component(value or fallback or unknown, unknown)

    return {
        "artist": _Value(text(meta.artist)),
        "album": _Value(text(meta.album)),
        "album_artist": _Value(text(meta.album_artist or meta.artist)),
        "title": _Value(text(meta.title)),
        "track": _Value(meta.track_number if meta.track_number is not None else 0),
        "disc": _Value(meta.disc_number if meta.disc_number is not None else 0),
        "year": _Value(meta.year if meta.year is not None else unknown),
        "ext": _Value(extension.lstrip(".").lower()),
        "filename": _Value(sanitize_component(meta.title, unknown)),
    }


def render_relative(
    template: str,
    meta: TrackMetadata,
    *,
    extension: str,
    source_filename: str = "",
    unknown: str = "Unknown",
) -> Path:
    """Render a template into a *relative* path (extension appended automatically)."""
    validate_template(template)
    values = _template_values(meta, extension, unknown)
    if source_filename:
        values["filename"] = _Value(sanitize_component(Path(source_filename).stem, unknown))
    rendered = _SafeFormatter().vformat(template, (), values)
    parts = [
        sanitize_component(part, unknown)
        for part in re.split(r"[/\\]+", rendered)
        if part.strip()
    ]
    if not parts:
        parts = [sanitize_component(meta.title, unknown)]
    if "{ext" not in template:
        parts[-1] = f"{parts[-1]}.{extension.lstrip('.').lower()}"
    relative = Path(*parts)
    if relative.is_absolute() or any(part in {".", ".."} for part in parts):
        raise ValueError("命名模板生成了非法路径")
    return relative


def target_path(
    library_root: Path,
    template: str,
    meta: TrackMetadata,
    *,
    extension: str,
    source_filename: str = "",
    unknown: str = "Unknown",
) -> Path:
    """Render and verify that the destination stays inside ``library_root``."""
    relative = render_relative(
        template,
        meta,
        extension=extension,
        source_filename=source_filename,
        unknown=unknown,
    )
    root = library_root.resolve()
    destination = (root / relative).resolve()
    if not destination.is_relative_to(root):
        raise ValueError("命名模板生成的目标路径越出音乐库目录")
    return destination


def example_target(template: str, unknown: str = "Unknown") -> str:
    meta = TrackMetadata(
        title="Bohemian Rhapsody",
        artist="Queen",
        album="A Night at the Opera",
        year=1975,
        track_number=11,
    )
    return str(
        render_relative(
            template,
            meta,
            extension=".flac",
            source_filename="11 Bohemian Rhapsody.flac",
            unknown=unknown,
        )
    )
