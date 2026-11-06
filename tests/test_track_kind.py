"""单曲 / 专辑曲目判定：目录里只有一首歌就是单曲，专辑目录里多首就是专辑曲目。

判定依据必须能说清（``kind_reason``），同目录音频数必须一次遍历统计出来（154 首不能
变成 154 次目录遍历），并且「单曲」不再被算成「缺 专辑轨号」。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from musicpilot.adapters.scanner import audio_counts, scan
from musicpilot.domain.library import classify_kind, missing_fields
from musicpilot.domain.models import TrackMetadata
from tests.audiofiles import make_flac, make_mp3

FULL_ALBUM_TAGS = {
    "title": "齐全", "artist": "歌手", "album": "专辑",
    "album_artist": "专辑歌手", "year": 2000, "track_number": 1,
}


# --------------------------------------------------------------------- 判定规则


def test_directory_with_single_audio_file_is_a_single() -> None:
    meta = TrackMetadata(title="杜康", artist="GAI周延", album="杜康")
    kind, reason = classify_kind(meta, sibling_audio_count=1)
    assert kind == "single"
    assert reason == "目录内仅此 1 个音频文件"


def test_directory_with_many_audio_files_is_an_album_track() -> None:
    meta = TrackMetadata(title="NOT GOOD", artist="Rapeter", album="IMGOOD别担心我")
    kind, reason = classify_kind(meta, sibling_audio_count=12)
    assert kind == "album"
    assert "12" in reason


def test_album_type_tag_wins_over_directory_count() -> None:
    meta = TrackMetadata(title="Villain", artist="某人", album="Villain")
    # 标签说这是 single 就走 single，哪怕目录里还躺着别的音频
    for tag in ("single", "Single", "musicbrainz_albumtype:single"):
        kind, reason = classify_kind(meta, sibling_audio_count=9, album_type=tag)
        assert kind == "single", tag
        assert tag in reason


@pytest.mark.parametrize(
    ("meta", "reason"),
    [
        (TrackMetadata(title="某歌", artist="歌手"), "缺少专辑标签"),
        (TrackMetadata(title="某歌", artist="歌手", album="某歌"), "专辑名与曲名相同"),
    ],
)
def test_single_hints_from_missing_or_self_titled_album(
    meta: TrackMetadata, reason: str
) -> None:
    """这两种旁证只在「数不出目录里有几首」时才拿来说话。"""
    kind, actual = classify_kind(meta, sibling_audio_count=None)
    assert kind == "single"
    assert actual == reason


def test_shared_directory_beats_the_weaker_single_hints() -> None:
    """目录里住了别的歌就不可能是单曲：否则缺专辑标签的专辑曲目永远补不上。"""
    for meta in (
        TrackMetadata(title="某歌", artist="歌手"),
        TrackMetadata(title="某歌", artist="歌手", album="某歌"),
    ):
        kind, _reason = classify_kind(meta, sibling_audio_count=10)
        assert kind == "album"


def test_unknown_directory_count_falls_back_to_album_track() -> None:
    """数不出目录里有几首就按专辑曲目处理：宁可多补，也不漏补。"""
    meta = TrackMetadata(title="某歌", artist="歌手", album="某专辑")
    kind, reason = classify_kind(meta, sibling_audio_count=None)
    assert kind == "album"
    assert reason == "默认按专辑曲目处理"


# --------------------------------------------------------------------- 缺字段


def test_single_only_requires_title_and_artist() -> None:
    single = TrackMetadata(title="杜康", artist="GAI周延", kind="single")
    assert single.missing_fields() == ()
    assert missing_fields(single) == []


def test_album_track_still_requires_six_fields() -> None:
    album = TrackMetadata(title="NOT GOOD", artist="Rapeter", kind="album")
    assert set(album.missing_fields()) == {
        "album", "album_artist", "year", "track_number"
    }


def test_album_track_missing_fields_unchanged_when_kind_is_blank() -> None:
    """在线候选没有 kind：仍按专辑曲目的六项算（既有语义不变）。"""
    meta = TrackMetadata(title="t", artist="a", album="al", year=2000, track_number=1)
    assert set(missing_fields(meta, has_cover=False)) == {"album_artist", "cover"}


# --------------------------------------------------------------------- 一次遍历


def test_audio_counts_walks_the_directory_list_once() -> None:
    base = Path("/library")
    paths = [base / "专辑" / f"{index:02d}.flac" for index in range(1, 43)]
    paths += [base / "单曲" / "杜康.flac"]
    counts = audio_counts(paths)
    assert counts[base / "专辑"] == 42
    assert counts[base / "单曲"] == 1


def test_scan_counts_sibling_files_once_per_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """154 首不能变成 154 次目录遍历：扫描只用路径列表归并出计数。"""
    album = tmp_path / "IMGOOD别担心我"
    for index in range(1, 9):
        make_mp3(album / f"{index:02d} - 歌{index}.mp3", {**FULL_ALBUM_TAGS, "title": f"歌{index}"})
    single = tmp_path / "杜康"
    make_mp3(single / "杜康 - GAI周延.mp3", {"title": "杜康", "artist": "GAI周延"})

    calls: list[Path] = []
    original = Path.iterdir

    def spy(self: Path) -> Any:
        calls.append(self)
        return original(self)

    monkeypatch.setattr(Path, "iterdir", spy)
    files, _warnings = scan(
        tmp_path,
        extensions=frozenset({".mp3"}),
        exclude_dirs=frozenset(),
    )
    assert len(files) == 9
    # 扫描本身一次目录都不该逐个文件地翻
    assert calls == []
    by_name = {item.path_object.name: item for item in files}
    assert by_name["杜康 - GAI周延.mp3"].local_metadata.kind == "single"
    assert by_name["01 - 歌1.mp3"].local_metadata.kind == "album"
    assert "8 个音频文件" in by_name["01 - 歌1.mp3"].local_metadata.kind_reason


def test_album_type_tag_marks_single_in_a_shared_directory(tmp_path: Path) -> None:
    directory = tmp_path / "合辑"
    make_flac(directory / "a.flac", {"title": "A", "artist": "甲"})
    make_flac(directory / "b.flac", {"title": "B", "artist": "乙"})
    # 给 a.flac 写上 albumtype=single：标签优先于目录计数
    from mutagen.flac import FLAC

    audio = FLAC(str(directory / "a.flac"))
    audio["albumtype"] = ["single"]
    audio.save()

    files, _warnings = scan(
        tmp_path, extensions=frozenset({".flac"}), exclude_dirs=frozenset()
    )
    by_name = {item.path_object.name: item for item in files}
    assert by_name["a.flac"].local_metadata.kind == "single"
    assert "单曲" not in by_name["a.flac"].local_metadata.kind_reason
    assert by_name["b.flac"].local_metadata.kind == "album"


# --------------------------------------------------------------------- 接口输出


def test_library_payload_carries_kind_and_stats(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    library = workspace["library"]
    # 单曲：独占一个目录
    make_mp3(library / "杜康" / "杜康 - GAI周延.mp3", {"title": "杜康", "artist": "GAI周延"})
    # 专辑：一个目录多首
    for index in (1, 2):
        make_mp3(library / "IMGOOD" / f"{index:02d} - 歌{index}.mp3",
                 {**FULL_ALBUM_TAGS, "title": f"歌{index}"})

    payload = client.get("/api/library").json()
    assert payload["stats"]["tracks"] == 3
    assert payload["stats"]["singles"] == 1
    assert payload["stats"]["album_tracks"] == 2

    by_name = {track["name"]: track for track in payload["tracks"]}
    assert by_name["杜康 - GAI周延.mp3"]["kind"] == "single"
    assert by_name["杜康 - GAI周延.mp3"]["kind_reason"] == "目录内仅此 1 个音频文件"
    assert by_name["01 - 歌1.mp3"]["kind"] == "album"


def test_scan_splits_missing_details_by_kind(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    library = workspace["library"]
    # 单曲缺歌手：只要求标题 / 歌手，不该出现「缺 专辑 / 专辑轨号」
    make_mp3(library / "只补一半" / "只补一半.mp3", {"title": "只补一半"})
    # 专辑曲目缺四项
    for index in (1, 2):
        make_mp3(library / "专辑" / f"{index:02d}.mp3",
                 {"title": f"歌{index}", "artist": "歌手"})

    payload = client.post("/api/library/scan").json()
    rows = {row["name"]: row for row in payload["tracks"]}
    assert rows["只补一半.mp3"]["kind"] == "single"
    assert rows["只补一半.mp3"]["missing"] == ["artist", "cover"]
    assert "专辑轨号" not in rows["只补一半.mp3"]["missing_labels"]
    assert rows["01.mp3"]["kind"] == "album"
    assert "专辑轨号" in rows["01.mp3"]["missing_labels"]

    assert payload["singles"]["incomplete"] == 1
    assert payload["album_tracks"]["incomplete"] == 2
    assert payload["incomplete"] == 3


# --------------------------------------------------------------------- 候选预览


def test_candidate_payload_explains_per_field_verdicts(
    client: TestClient, workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """预览窗要的是「当前值 / 候选值 / 会不会写入」，已有值一律不覆盖。"""
    from tests.test_library import FakeProvider, _client_context

    library = workspace["library"]
    path = make_mp3(library / "专辑" / "01 - 晴天.mp3", {"title": "晴天", "artist": "周杰伦"})
    make_mp3(library / "专辑" / "02 - 稻香.mp3", {"title": "稻香", "artist": "周杰伦"})
    context = _client_context(client)
    context.metadata.providers["netease"] = FakeProvider([
        TrackMetadata(
            title="晴天", artist="别人", album="叶惠美", album_artist="周杰伦",
            year=2003, track_number=3, source="netease", external_id="1",
            lyrics="[00:01.00]一句词",
        )
    ])
    payload = client.post(
        "/api/library/search", json={"path": str(path), "source": "netease"}
    ).json()
    item = payload["results"][0]

    rows = {row["key"]: row for row in item["fields"]}
    # 已有值：候选值照给，但标「不覆盖」，也不进将写入清单
    assert rows["title"]["current"] == "晴天"
    assert rows["title"]["candidate"] == "晴天"
    assert rows["title"]["keep"] is True and rows["title"]["will_write"] is False
    assert rows["artist"]["keep"] is True
    # 空的：会写
    assert rows["album"]["current"] == ""
    assert rows["album"]["will_write"] is True
    assert rows["lyrics"]["will_write"] is True

    assert set(item["would_write"]) == {"album", "album_artist", "year", "track_number", "lyrics"}
    assert "专辑艺术家" in item["would_write_labels"]
    assert item["has_lyrics"] is True


def test_candidate_without_lyrics_is_labelled_honestly(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    from tests.test_library import FakeProvider, _client_context

    path = make_mp3(workspace["library"] / "a" / "x.mp3", {"title": "某歌", "artist": "歌手"})
    make_mp3(workspace["library"] / "a" / "y.mp3", {"title": "另一首", "artist": "歌手"})
    _client_context(client).metadata.providers["qq"] = FakeProvider([
        TrackMetadata(title="某歌", artist="歌手", album="专辑", source="qq", external_id="2")
    ])
    item = client.post("/api/library/search", json={"path": str(path), "source": "qq"}).json()[
        "results"
    ][0]
    assert item["has_lyrics"] is False
    rows = {row["key"]: row for row in item["fields"]}
    assert rows["lyrics"]["candidate"] == ""
    assert rows["lyrics"]["will_write"] is False
    assert "lyrics" not in item["would_write"]


def test_apply_on_a_single_does_not_report_album_gaps(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    """补完单曲后不该再报「还缺 专辑 / 专辑轨号」——它就是没有这些。"""
    path = make_mp3(
        workspace["library"] / "杜康" / "杜康 - GAI周延.mp3", {"title": "杜康", "artist": "GAI周延"}
    )
    payload = client.post("/api/library/apply", json={
        "path": str(path),
        "metadata": {"title": "杜康", "artist": "GAI周延", "source": "netease", "external_id": "1"},
        "dry_run": True,
    }).json()
    assert payload["still_missing"] in ([], ["cover"])
    assert "专辑轨号" not in payload["still_missing_labels"]
