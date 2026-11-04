from __future__ import annotations

from pathlib import Path

import pytest

from musicpilot.domain.models import TrackMetadata
from musicpilot.domain.naming import (
    DEFAULT_TEMPLATE,
    example_target,
    render_relative,
    sanitize_component,
    target_path,
    validate_template,
)

META = TrackMetadata(
    title="Bohemian Rhapsody",
    artist="Queen",
    album="A Night at the Opera",
    year=1975,
    track_number=11,
)


def test_default_template() -> None:
    result = render_relative(
        DEFAULT_TEMPLATE, META, extension=".flac", source_filename="11 song.flac"
    )
    assert result == Path("Queen/A Night at the Opera/11 - Bohemian Rhapsody.flac")


def test_year_and_unknown_placeholder() -> None:
    template = "{artist} ({year})/{album}/{track:02d} {title}"
    result = render_relative(template, META, extension=".mp3")
    assert result == Path("Queen (1975)/A Night at the Opera/11 Bohemian Rhapsody.mp3")
    empty = TrackMetadata()
    result = render_relative(DEFAULT_TEMPLATE, empty, extension=".flac", unknown="未知")
    assert result.parts[0] == "未知"


def test_sanitize_illegal_characters() -> None:
    assert sanitize_component('A/B:C*D?E"F') == "A_B_C_D_E_F"
    assert sanitize_component("...") == "Unknown"
    assert sanitize_component("CON") == "_CON"
    assert len(sanitize_component("x" * 400)) == 120


@pytest.mark.parametrize(
    "template",
    [
        "{unknown_field}",
        "{artist.__class__}",
        "{artist[0]}",
        "{artist",
        "",
    ],
)
def test_rejects_bad_templates(template: str) -> None:
    with pytest.raises(ValueError):
        validate_template(template)


def test_target_stays_inside_library(tmp_path: Path) -> None:
    library = tmp_path / "music"
    library.mkdir()
    result = target_path(
        library, "{artist}/../../{title}", META, extension=".flac"
    )
    assert result.is_relative_to(library.resolve())
    assert ".." not in result.parts


def test_explicit_ext_in_template() -> None:
    result = render_relative("{artist}/{title}.{ext}", META, extension="FLAC")
    assert result.suffix == ".flac"
    assert str(result).endswith("Bohemian Rhapsody.flac")


def test_example_target() -> None:
    assert example_target(DEFAULT_TEMPLATE).endswith("11 - Bohemian Rhapsody.flac")
