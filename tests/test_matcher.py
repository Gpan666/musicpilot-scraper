from __future__ import annotations

from pathlib import Path

from musicpilot.domain.matcher import (
    choose_best,
    merge_metadata,
    normalize,
    parse_filename,
    score_candidate,
    similarity,
)
from musicpilot.domain.models import TrackMetadata


def test_parse_track_artist_title() -> None:
    parsed = parse_filename(Path("/inbox/01 - Queen - Bohemian Rhapsody.flac"))
    assert parsed.track_number == 1
    assert parsed.artist == "Queen"
    assert parsed.title == "Bohemian Rhapsody"


def test_parse_directory_hints() -> None:
    parsed = parse_filename(Path("/inbox/Queen/A Night at the Opera/11 Bohemian.flac"))
    assert parsed.album == "A Night at the Opera"
    assert parsed.track_number == 11
    assert parsed.year is None or isinstance(parsed.year, int)


def test_source_root_name_is_not_used_as_album(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    parsed = parse_filename(root / "01 - Queen - Bohemian Rhapsody.flac", root=root)
    assert parsed.album is None
    assert parsed.artist == "Queen"
    nested = parse_filename(root / "A Night at the Opera" / "11 Song.flac", root=root)
    assert nested.album == "A Night at the Opera"


def test_normalize_and_similarity() -> None:
    assert normalize("Ｂｏｈｅｍｉａｎ  （Official Video）") == "bohemian"
    assert similarity("Bohemian Rhapsody", "bohemian rhapsody") == 1.0
    assert similarity("Bohemian Rhapsody", "We Are The Champions") < 0.5


def test_scoring_prefers_exact_match() -> None:
    query = TrackMetadata(title="Bohemian Rhapsody", artist="Queen", album="A Night at the Opera")
    good = TrackMetadata(
        title="Bohemian Rhapsody",
        artist="Queen",
        album="A Night at the Opera",
        year=1975,
        source="musicbrainz",
    )
    bad = TrackMetadata(title="We Are The Champions", artist="Queen", source="musicbrainz")
    assert score_candidate(query, good) > score_candidate(query, bad)
    best = choose_best(query, [bad, good], threshold=0.62)
    assert best is not None and best.metadata.title == "Bohemian Rhapsody"


def test_low_confidence_is_rejected() -> None:
    query = TrackMetadata(title="Bohemian Rhapsody", artist="Queen")
    unrelated = TrackMetadata(title="Yesterday", artist="The Beatles", source="musicbrainz")
    assert choose_best(query, [unrelated], threshold=0.8) is None


def test_merge_only_fills_missing_fields() -> None:
    local = TrackMetadata(title="My Title", artist="Local Artist", source="tags")
    remote = TrackMetadata(
        title="Online Title",
        artist="Online Artist",
        album="Online Album",
        year=1975,
        source="musicbrainz",
    )
    merged = merge_metadata(local, remote)
    assert merged.title == "My Title"
    assert merged.artist == "Local Artist"
    assert merged.album == "Online Album"
    assert merged.year == 1975
    assert merged.source == "musicbrainz"
