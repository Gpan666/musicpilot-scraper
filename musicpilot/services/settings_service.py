from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from musicpilot.adapters.store import Store
from musicpilot.config import RuntimeSettings, Settings
from musicpilot.domain.naming import TEMPLATE_PRESETS, example_target, validate_template

EDITABLE_FIELDS = tuple(RuntimeSettings.model_fields)

# 设置项的中文名，校验失败时告诉用户到底哪一项不对
FIELD_LABELS = {
    "source_dir": "源目录",
    "library_dir": "音乐库目录",
    "transfer_mode": "转移方式",
    "naming_template": "命名模板",
    "conflict_policy": "目标冲突策略",
    "symlink_relative": "软链接使用相对路径",
    "write_tags": "写入音频标签",
    "embed_cover": "内嵌封面",
    "write_lyrics": "写入歌词",
    "metadata_sources": "在线元数据源",
    "confidence_threshold": "置信度阈值",
    "unknown_placeholder": "缺失字段占位符",
    "watch_enabled": "自动刮削开关",
    "watch_interval": "检查间隔",
    "watch_settle": "稳定等待",
}
LIMIT_LABELS = {
    "greater_than_equal": "不能小于",
    "less_than_equal": "不能大于",
    "string_too_short": "不能为空",
    "string_too_long": "长度超限",
    "missing": "不能缺失",
    "bool_parsing": "需要是「开」或「关」",
    "enum": "取值不在可选范围内",
    "literal_error": "取值不在可选范围内",
    "float_parsing": "需要是数字",
    "int_parsing": "需要是整数",
    "list_type": "需要是列表",
    "value_error": "取值不合法",
}


def describe_validation_error(exc: ValidationError) -> str:
    """把 pydantic 的英文校验信息翻成一句中文。"""
    messages: list[str] = []
    for error in exc.errors()[:3]:
        parts = [str(part) for part in error.get("loc") or ()]
        field = parts[0] if parts else ""
        label = FIELD_LABELS.get(field, field or "设置项")
        kind = str(error.get("type") or "")
        limit = error.get("ctx") or {}
        bound = limit.get("ge", limit.get("le", limit.get("min_length", limit.get("max_length"))))
        if kind in {"literal_error", "enum"} and len(parts) > 1:
            label = f"{label}（第 {parts[1]} 项）"
        text = LIMIT_LABELS.get(kind, "取值不合法")
        if bound is not None and kind in {"greater_than_equal", "less_than_equal"}:
            text = f"{text} {bound}"
        messages.append(f"{label}{text}" if kind != "missing" else f"缺少{label}")
    return "；".join(messages) or "设置项不合法"


class SettingsError(ValueError):
    pass


class SettingsService:
    def __init__(self, base: Settings, store: Store) -> None:
        self.base = base
        self.store = store

    def defaults(self) -> dict[str, Any]:
        return self.base.runtime_defaults()

    def get(self) -> RuntimeSettings:
        merged = {**self.defaults(), **self.store.get_overrides()}
        try:
            return RuntimeSettings.model_validate(merged)
        except ValidationError as exc:
            raise SettingsError(f"已保存的设置无效：{describe_validation_error(exc)}") from exc

    def describe(self) -> dict[str, Any]:
        runtime = self.get()
        try:
            example = example_target(runtime.naming_template, runtime.unknown_placeholder)
        except ValueError as exc:
            example = f"模板无效：{exc}"
        return {
            "settings": runtime.model_dump(mode="json"),
            "defaults": self.defaults(),
            "editable_fields": list(EDITABLE_FIELDS),
            "templates": list(TEMPLATE_PRESETS),
            "template_fields": sorted(
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
            ),
            "example_target": example,
        }

    def update(self, patch: dict[str, Any]) -> RuntimeSettings:
        unknown = sorted(set(patch) - set(EDITABLE_FIELDS))
        if unknown:
            raise SettingsError(f"不允许修改的字段：{', '.join(unknown)}")
        candidate = {**self.get().model_dump(mode="json"), **patch}
        try:
            runtime = RuntimeSettings.model_validate(candidate)
        except ValidationError as exc:
            raise SettingsError(f"设置校验失败：{describe_validation_error(exc)}") from exc
        try:
            validate_template(runtime.naming_template)
        except ValueError as exc:
            raise SettingsError(f"命名模板无效：{exc}") from exc
        source = Path(runtime.source_dir).resolve()
        library = Path(runtime.library_dir).resolve()
        if source == library or source.is_relative_to(library) or library.is_relative_to(source):
            raise SettingsError("源目录与音乐库目录不能相同或互相嵌套，避免整理时覆盖源文件")
        if "local" not in runtime.metadata_sources:
            runtime.metadata_sources.insert(0, "local")
        self.store.replace_overrides(runtime.model_dump(mode="json"))
        return runtime
