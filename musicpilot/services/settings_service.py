from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from musicpilot.adapters.store import Store
from musicpilot.config import RuntimeSettings, Settings
from musicpilot.domain.naming import TEMPLATE_PRESETS, example_target, validate_template

EDITABLE_FIELDS = tuple(RuntimeSettings.model_fields)


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
            raise SettingsError(f"已保存的设置无效：{exc}") from exc

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
            raise SettingsError(f"设置校验失败：{exc}") from exc
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
