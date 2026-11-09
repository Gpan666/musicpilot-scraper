"""音乐库排序：五个维度 × 升降序，缺值恒定垫底，参数不认识就退回默认。

界面上的排序列头点出来的是 ``(维度, 方向)``，落到的就是这里的 ``sort_tracks``；
所以这里覆盖的是「谁在前、谁在后」这件事本身，而不是某个页面的实现。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from musicpilot.domain.sorting import (
    SORT_FIELDS,
    normalize,
    sort_payload,
    sort_tracks,
)


def _track(**values: Any) -> dict[str, Any]:
    base = {
        "path": "/lib/x.mp3",
        "name": "x.mp3",
        "created_at": 0.0,
        "modified_at": 0.0,
        "duration": None,
        "album": None,
        "artist": None,
        "track_number": None,
        "disc_number": None,
    }
    base.update(values)
    return base


TRACKS = [
    _track(path="/lib/b.mp3", name="b.mp3", created_at=300.0, modified_at=100.0,
           duration=200.0, album="Blue", artist="Zed", track_number=2),
    _track(path="/lib/a.mp3", name="a.mp3", created_at=100.0, modified_at=300.0,
           duration=None, album=None, artist=None),
    _track(path="/lib/c.mp3", name="c.mp3", created_at=200.0, modified_at=200.0,
           duration=100.0, album="Amber", artist="Amy", track_number=1),
]
NAMES = {track["path"]: track["name"] for track in TRACKS}


def _order(field: str, direction: str) -> list[str]:
    return [item["name"] for item in sort_tracks(TRACKS, field, direction)]


@pytest.mark.parametrize(
    ("field", "direction", "expected"),
    [
        ("added", "asc", ["a.mp3", "c.mp3", "b.mp3"]),
        ("added", "desc", ["b.mp3", "c.mp3", "a.mp3"]),
        ("modified", "asc", ["b.mp3", "c.mp3", "a.mp3"]),
        ("modified", "desc", ["a.mp3", "c.mp3", "b.mp3"]),
        ("album", "asc", ["c.mp3", "b.mp3", "a.mp3"]),
        ("album", "desc", ["b.mp3", "c.mp3", "a.mp3"]),
        ("artist", "asc", ["c.mp3", "b.mp3", "a.mp3"]),
        ("artist", "desc", ["b.mp3", "c.mp3", "a.mp3"]),
    ],
)
def test_sort_dimensions_and_directions(field: str, direction: str, expected: list[str]) -> None:
    assert _order(field, direction) == expected


def test_duration_puts_unknown_last_in_both_directions() -> None:
    """时长读不出来的那首永远在最后：升序降序都不许把「—」顶到第一行。"""
    assert _order("duration", "asc") == ["c.mp3", "b.mp3", "a.mp3"]
    assert _order("duration", "desc") == ["b.mp3", "c.mp3", "a.mp3"]


def test_album_missing_goes_last_even_descending() -> None:
    assert _order("album", "desc")[-1] == "a.mp3"
    assert _order("album", "asc")[-1] == "a.mp3"


def test_default_order_is_untouched() -> None:
    assert [item["name"] for item in sort_tracks(TRACKS)] == ["b.mp3", "a.mp3", "c.mp3"]


def test_unknown_field_or_direction_falls_back_to_default() -> None:
    assert normalize("bogus", "sideways") == ("default", "asc")
    assert normalize(None, None) == ("default", "asc")
    assert normalize("ALBUM", "DESC") == ("album", "desc")


def test_sort_payload_lists_every_dimension() -> None:
    ids = [item["id"] for item in sort_payload()]
    assert ids == [field.id for field in SORT_FIELDS]
    assert {"added", "modified", "duration", "album", "artist"} <= set(ids)


def _seed_library(workspace: dict[str, Any]) -> None:
    """三首真 mp3：两首同专辑（一首缺时长无关），一首什么标签都没有。"""
    from tests.audiofiles import make_mp3

    library = workspace["library"]
    make_mp3(library / "b.mp3", {"title": "Blue", "album": "Blue", "artist": "Zed", "tracknumber": 2})
    make_mp3(library / "a.mp3", {"title": "Plain"})
    make_mp3(library / "c.mp3", {"title": "Amber", "album": "Amber", "artist": "Amy", "tracknumber": 1})


def test_library_endpoint_returns_sorted_tracks(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    """/api/library 接受 sort / direction，并把当前排法一起回给前端。"""
    _seed_library(workspace)
    payload = client.get("/api/library", params={"sort": "album", "direction": "desc"}).json()
    assert payload["sort"] == "album"
    assert payload["direction"] == "desc"
    assert all(item["created_at"] for item in payload["tracks"])
    # 缺专辑的曲目排在最后，且不管升序降序都不会跑到前面
    albums = [item["album"] for item in payload["tracks"]]
    filled = [album for album in albums if album]
    assert albums[len(filled):] == [None] * (len(albums) - len(filled))


def test_library_endpoint_accepts_unknown_sort(client: TestClient) -> None:
    payload = client.get("/api/library", params={"sort": "nope", "direction": "nope"}).json()
    assert (payload["sort"], payload["direction"]) == ("default", "asc")


def test_library_sort_by_duration_keeps_unknown_last(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    _seed_library(workspace)
    payload = client.get("/api/library", params={"sort": "duration", "direction": "desc"}).json()
    durations = [item["duration"] for item in payload["tracks"]]
    known = [value for value in durations if value]
    assert known == sorted(known, reverse=True)
    assert durations[len(known):] == [None] * (len(durations) - len(known))
