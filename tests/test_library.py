"""音乐库：自动枚举、横向标签字段、卡片详情、手动刮削（匹配度 1–10）、
以及「自动扫描查漏补缺只改标签、绝不动文件位置」这条硬约束。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from musicpilot.adapters.metadata.base import (
    REASON_NO_LYRICS,
    REASON_NO_RESPONSE,
    REASON_UNSUPPORTED,
)
from musicpilot.adapters.metadata.service import MetadataService
from musicpilot.adapters.store import Store
from musicpilot.config import load_settings
from musicpilot.domain.library import fill_from, match_score, missing_fields
from musicpilot.domain.models import TrackMetadata
from tests.audiofiles import make_mp3, read_easy, tag_value

# --------------------------------------------------------------------- 工具


def _snapshot(root: Path) -> dict[str, tuple[int, int, bool]]:
    """记录目录里每个文件的 (大小, mtime, 是否软链接)，用来断言「没被动过」。"""
    return {
        str(path): (path.stat().st_size, path.stat().st_mtime_ns, path.is_symlink())
        for path in sorted(root.rglob("*"))
        if path.is_file() or path.is_symlink()
    }


class FakeProvider:
    """替身音源：只按要求回候选，不联网。"""

    def __init__(self, candidates: list[TrackMetadata]) -> None:
        self.candidates = candidates
        self.calls: list[dict[str, Any]] = []

    async def search(self, *, title: str, artist: str | None = None, album: str | None = None,
                     limit: int = 5) -> list[TrackMetadata]:
        self.calls.append({"title": title, "artist": artist, "album": album, "limit": limit})
        return self.candidates[:limit]


def _enable_tag_writing(client: TestClient) -> None:
    """conftest 默认 MP_WRITE_TAGS=false；写标签相关的用例显式打开。"""
    response = client.put("/api/settings", json={"values": {"write_tags": True}})
    assert response.status_code == 200, response.text


def _client_context(client: TestClient) -> Any:
    return client.app.state.context  # type: ignore[attr-defined]


def _metadata_service(context: Any) -> MetadataService:
    service: MetadataService = context.metadata
    if service.client is None:
        service.client = _DummyClient()
    return service


class _DummyClient:
    async def aclose(self) -> None:
        return None


# --------------------------------------------------------------------- 单元


def test_match_score_is_always_between_one_and_ten() -> None:
    query = TrackMetadata(title="Bohemian Rhapsody", artist="Queen", album="A Night at the Opera")
    same = match_score(query, query)
    other = match_score(query, TrackMetadata(title="完全无关的歌", artist="别人"))
    assert same == 10
    assert 1 <= other <= 10
    assert other < same


def test_match_score_ranks_closer_titles_higher() -> None:
    query = TrackMetadata(title="晴天", artist="周杰伦", album="叶惠美")
    close = match_score(query, TrackMetadata(title="晴天", artist="周杰伦"))
    far = match_score(query, TrackMetadata(title="稻香", artist="周杰伦"))
    assert close > far


def test_missing_fields_counts_cover_and_album_artist() -> None:
    meta = TrackMetadata(title="t", artist="a", album="al", year=2000, track_number=1)
    missing = missing_fields(meta, has_cover=False)
    assert set(missing) == {"album_artist", "cover"}


def test_fill_from_never_overwrites_known_values() -> None:
    local = TrackMetadata(title="本地标题", artist="本地歌手", lyrics="本地歌词")
    remote = TrackMetadata(
        title="在线标题",
        artist="在线歌手",
        album="在线专辑",
        album_artist="在线专辑歌手",
        year=1999,
        track_number=7,
        lyrics="在线歌词",
    )
    merged = fill_from(local, remote)
    assert merged.title == "本地标题"
    assert merged.artist == "本地歌手"
    assert merged.lyrics == "本地歌词"
    assert merged.album == "在线专辑"
    assert merged.track_number == 7
    # 本地没有专辑艺术家时回落到歌手，不让它为「空」
    assert merged.album_artist == "在线专辑歌手"


# --------------------------------------------------------------------- 列表


def test_library_lists_every_audio_file(client: TestClient, workspace: dict[str, Any]) -> None:
    library = workspace["library"]
    make_mp3(library / "Queen" / "Opera" / "01 - Bohemian Rhapsody.mp3", {
        "title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera",
        "album_artist": "Queen", "track_number": 11,
    })
    make_mp3(library / "周杰伦" / "叶惠美" / "03 - 晴天.mp3", {
        "title": "晴天", "artist": "周杰伦", "album": "叶惠美", "track_number": 3,
    })
    (library / "notes.txt").write_text("不是音频", encoding="utf-8")

    payload = client.get("/api/library").json()
    assert payload["library_dir"] == str(library)
    assert payload["total"] == 2
    names = {track["name"] for track in payload["tracks"]}
    assert names == {"01 - Bohemian Rhapsody.mp3", "03 - 晴天.mp3"}
    assert "notes.txt" not in names


def test_library_list_carries_horizontal_tag_fields(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    """横向标签要展示：封面、歌手、专辑名、专辑轨号、专辑艺术家、歌词预览。"""
    make_mp3(workspace["library"] / "Queen" / "01 - Bohemian Rhapsody.mp3", {
        "title": "Bohemian Rhapsody", "artist": "Queen", "album": "A Night at the Opera",
        "album_artist": "Queen", "track_number": 11, "year": 1975,
    })
    track = client.get("/api/library").json()["tracks"][0]
    for key in ("cover_url", "artist", "album", "track_number", "album_artist",
                "lyrics_preview", "has_lyrics", "title", "relative_dir"):
        assert key in track, key
    assert track["artist"] == "Queen"
    assert track["album"] == "A Night at the Opera"
    assert track["track_number"] == 11
    assert track["album_artist"] == "Queen"
    assert track["relative_dir"] == "Queen"


def test_library_lyrics_preview_is_truncated(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    from mutagen.id3 import ID3, USLT

    path = make_mp3(workspace["library"] / "a.mp3", {"title": "有歌词的歌"})
    tags = ID3(str(path))
    tags.add(USLT(encoding=3, lang="chi", desc="", text="第一行\n" * 60))
    tags.save(str(path))

    track = client.get("/api/library").json()["tracks"][0]
    assert track["has_lyrics"] is True
    assert track["lyrics_preview"].endswith("…")
    assert len(track["lyrics_preview"]) <= 121
    detail = client.get("/api/library/detail", params={"path": track["path"]}).json()
    assert "第一行" in detail["lyrics"]
    assert len(detail["lyrics"]) > len(track["lyrics_preview"])


def test_library_detail_lists_missing_fields_and_sources(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    # 独占一个目录、又没有专辑标签 → 单曲：只要求标题与歌手，不要求专辑 / 轨号
    make_mp3(workspace["library"] / "bare.mp3", {"title": "只有标题"})
    track = client.get("/api/library").json()["tracks"][0]
    detail = client.get("/api/library/detail", params={"path": track["path"]}).json()
    assert detail["kind"] == "single"
    assert detail["kind_reason"] == "目录内仅此 1 个音频文件"
    assert "artist" in detail["missing"]
    assert "album" not in detail["missing"]
    assert detail["metadata"]["title"] == "只有标题"
    assert {item["id"] for item in detail["sources"]} == {
        "musicbrainz", "netease", "qq", "kugou", "kuwo"
    }
    assert all("label" in item for item in detail["sources"])


def test_library_rejects_paths_outside_the_library(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    outside = make_mp3(workspace["source"] / "outside.mp3")
    assert client.get("/api/library/detail", params={"path": str(outside)}).status_code == 404
    assert client.post("/api/library/search", json={"path": str(outside)}).status_code == 404


def test_library_cover_route_serves_embedded_art(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "a.mp3", {"title": "无封面"})
    track = client.get("/api/library").json()["tracks"][0]
    assert track["cover_url"] is None
    assert client.get("/api/library/cover", params={"path": str(path)}).status_code == 404


# --------------------------------------------------------------------- 手动刮削


def test_manual_scrape_search_shows_scores_one_to_ten(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "q" / "01 - 晴天.mp3", {
        "title": "晴天", "artist": "周杰伦", "album": "叶惠美",
    })
    # 同目录多一首 → 专辑曲目，专辑艺术家 / 轨号 / 年份都算「该有」，候选能补的才有的可补
    make_mp3(workspace["library"] / "q" / "02 - 稻香.mp3", {"title": "稻香", "artist": "周杰伦"})
    context = _client_context(client)
    provider = FakeProvider([
        TrackMetadata(title="晴天", artist="周杰伦", album="叶惠美", album_artist="周杰伦",
                      track_number=3, source="netease", external_id="1"),
        TrackMetadata(title="稻香", artist="周杰伦", album="魔杰座", source="netease",
                      external_id="2"),
    ])
    context.metadata.providers["netease"] = provider

    payload = client.post(
        "/api/library/search", json={"path": str(path), "source": "netease"}
    ).json()
    assert payload["searched"] == ["netease"]
    assert provider.calls[0]["title"] == "晴天"
    scores = [item["score"] for item in payload["results"]]
    assert all(isinstance(score, int) and 1 <= score <= 10 for score in scores)
    assert scores == sorted(scores, reverse=True)
    best = payload["results"][0]
    assert best["source_label"] == "网易云"
    assert {"album_artist", "track_number"} <= set(best["fills"])


def test_manual_scrape_respects_the_chosen_platform(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "t.mp3", {"title": "某歌"})
    context = _client_context(client)
    netease = FakeProvider([TrackMetadata(title="某歌", source="netease", external_id="1")])
    qq = FakeProvider([TrackMetadata(title="某歌", source="qq", external_id="2")])
    context.metadata.providers.update({"netease": netease, "qq": qq})
    # 「搜全部平台」只搜设置里启用的那些，先把两个平台打开
    assert client.put(
        "/api/settings", json={"values": {"metadata_sources": ["local", "netease", "qq"]}}
    ).status_code == 200

    client.post("/api/library/search", json={"path": str(path), "source": "qq"})
    assert qq.calls and not netease.calls

    both = client.post("/api/library/search", json={"path": str(path), "source": ""}).json()
    assert set(both["searched"]) == {"netease", "qq"}
    assert netease.calls


def test_manual_scrape_rejects_unknown_platform(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "t.mp3", {"title": "某歌"})
    response = client.post(
        "/api/library/search", json={"path": str(path), "source": "piracy-bay"}
    )
    assert response.status_code == 400


def test_manual_scrape_apply_writes_only_missing_tags(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "a" / "b.mp3", {"title": "本地标题", "artist": "本地歌手"})
    _enable_tag_writing(client)
    before = _snapshot(workspace["library"])

    payload = client.post("/api/library/apply", json={
        "path": str(path),
        "metadata": {
            "title": "在线标题", "artist": "在线歌手", "album": "在线专辑",
            "album_artist": "在线专辑歌手", "year": 2001, "track_number": 4,
            "source": "netease", "external_id": "42",
        },
    }).json()
    assert payload["moved"] is False
    assert set(payload["written"]) == {"album", "album_artist", "year", "track_number"}
    assert tag_value(path, "title") == "本地标题"
    assert tag_value(path, "artist") == "本地歌手"
    assert tag_value(path, "album") == "在线专辑"
    assert tag_value(path, "albumartist") == "在线专辑歌手"
    assert tag_value(path, "tracknumber") == "4"

    after = _snapshot(workspace["library"])
    assert set(after) == set(before), "补全不得新增/移动/复制文件"
    assert not (workspace["library"] / "在线专辑").exists()


def test_manual_scrape_apply_dry_run_writes_nothing(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "dry.mp3", {"title": "标题"})
    before = read_easy(path)
    payload = client.post("/api/library/apply", json={
        "path": str(path),
        "metadata": {"title": "标题", "album": "专辑", "source": "qq"},
        "dry_run": True,
    }).json()
    assert payload["would_write"] == ["album"]
    assert read_easy(path) == before


def test_manual_scrape_apply_includes_lyrics(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "l.mp3", {"title": "带歌词"})
    _enable_tag_writing(client)
    payload = client.post("/api/library/apply", json={
        "path": str(path),
        "metadata": {
            "title": "带歌词", "artist": "歌手", "source": "netease",
            "external_id": "9", "lyrics": "[00:01.00]第一句\n[00:02.00]第二句",
        },
    }).json()
    assert "lyrics" in payload["written"]
    track = client.get("/api/library/detail", params={"path": str(path)}).json()
    assert "第一句" in track["lyrics"]


def test_lyrics_only_endpoint_updates_lyrics_tag(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "only-lyrics.mp3", {"title": "歌"})
    _enable_tag_writing(client)
    response = client.put("/api/library/lyrics", json={
        "path": str(path), "lyrics": "[00:01.00]只有歌词\n",
    })
    assert response.status_code == 200
    assert response.json()["written"] == ["lyrics"]
    assert client.get("/api/library/detail", params={"path": str(path)}).json()["lyrics"]


# --------------------------------------------------------------------- 查漏补缺


def test_scan_reports_incomplete_tracks_without_writing(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    make_mp3(workspace["library"] / "full.mp3", {
        "title": "齐全", "artist": "歌手", "album": "专辑",
        "album_artist": "专辑歌手", "year": 2000, "track_number": 1,
    })
    # 同目录两首 + 有专辑标签 → 专辑曲目，六项照旧
    make_mp3(workspace["library"] / "half.mp3", {"title": "缺一半", "artist": "歌手", "album": "专辑"})
    before = _snapshot(workspace["library"])

    payload = client.post("/api/library/scan").json()
    half = next(row for row in payload["tracks"] if row["name"] == "half.mp3")
    assert {"album_artist", "year", "track_number"} <= set(half["missing"])
    assert "album" not in half["missing"], "专辑已经写了，不该再算缺"
    assert half["missing_labels"]
    assert half["kind"] == "album"
    # full.mp3 字段齐全，只是没有内嵌封面，所以也会被列出来
    full = next(row for row in payload["tracks"] if row["name"] == "full.mp3")
    assert full["missing"] == ["cover"]
    assert payload["incomplete"] == 2
    assert _snapshot(workspace["library"]) == before, "扫描不能写任何东西"


def test_fill_completes_metadata_without_touching_files(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    """核心约束：补全后文件没被移动/复制/硬链接/软链接。"""
    library = workspace["library"]
    path = make_mp3(library / "专辑" / "01 - 歌.mp3", {"title": "歌", "artist": "歌手"})
    # 造一个音乐库内的软链接文件，补全后它还得是软链接，指向不变
    link = library / "链接.mp3"
    link.symlink_to(path)

    before = _snapshot(library)
    _enable_tag_writing(client)
    inode_before = path.stat().st_ino

    context = _client_context(client)
    context.metadata.providers["netease"] = FakeProvider([
        TrackMetadata(
            title="歌", artist="歌手", album="专辑", album_artist="专辑歌手",
            year=2005, track_number=1, source="netease", external_id="7",
        )
    ])

    payload = client.post("/api/library/fill", json={"sources": ["netease"]}).json()
    assert payload["moved"] == 0
    assert payload["filled"] >= 1
    assert "文件位置" in payload["note"]

    after = _snapshot(library)
    assert set(after) == set(before), "补全不得新增、移动或复制文件"
    assert all(after[key][2] == value[2] for key, value in before.items()), "软链接性质不得改变"
    assert link.is_symlink() and os.readlink(link) == str(path), "链接目标不得改写"
    assert path.stat().st_ino == inode_before, "文件本体不能被替换"

    assert tag_value(path, "album") == "专辑"
    assert tag_value(path, "albumartist") == "专辑歌手"
    assert tag_value(path, "date") == "2005"
    # 软链接与文件是同一份数据，标签自然一起补上
    assert tag_value(link, "album") == "专辑"


def test_fill_dry_run_writes_nothing(client: TestClient, workspace: dict[str, Any]) -> None:
    path = make_mp3(workspace["library"] / "dry-fill.mp3", {"title": "歌", "artist": "歌手"})
    before = read_easy(path)
    context = _client_context(client)
    context.metadata.providers["qq"] = FakeProvider([
        TrackMetadata(title="歌", artist="歌手", album="专辑", source="qq", external_id="1")
    ])
    payload = client.post(
        "/api/library/fill", json={"sources": ["qq"], "dry_run": True}
    ).json()
    assert payload["filled"] >= 1
    assert read_easy(path) == before


def test_fill_skips_tracks_with_low_match_score(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    _enable_tag_writing(client)
    path = make_mp3(workspace["library"] / "weak.mp3", {"title": "晴天", "artist": "周杰伦"})
    before = read_easy(path)
    context = _client_context(client)
    context.metadata.providers["netease"] = FakeProvider([
        TrackMetadata(title="完全无关的歌", artist="别人的乐队", album="另一张专辑",
                      source="netease", external_id="3")
    ])
    response = client.post("/api/library/fill", json={"sources": ["netease"]})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["filled"] == 0
    assert read_easy(path) == before


def test_fill_requires_tag_writing_enabled(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    make_mp3(workspace["library"] / "x.mp3", {"title": "歌"})
    client.put("/api/settings", json={"values": {"write_tags": False}})
    assert client.post("/api/library/fill", json={"sources": ["netease"]}).status_code == 400
    # 预览模式仍然可用，不会写任何东西
    assert client.post(
        "/api/library/fill", json={"sources": ["netease"], "dry_run": True}
    ).status_code == 200


def test_fill_endpoint_never_calls_transfer(
    client: TestClient, workspace: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """把转移函数换成「被调用就炸」，确保查漏补缺根本走不到搬运代码。"""
    import musicpilot.domain.transfer as transfer

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("音乐库补全绝不允许调用转移函数")

    monkeypatch.setattr(transfer, "transfer", _boom)
    monkeypatch.setattr("musicpilot.services.scraper.transfer", _boom, raising=False)

    path = make_mp3(workspace["library"] / "safe.mp3", {"title": "歌", "artist": "歌手"})
    _enable_tag_writing(client)
    client.app.state.context.metadata.providers["netease"] = FakeProvider([
        TrackMetadata(title="歌", artist="歌手", album="专辑", album_artist="歌手",
                      source="netease", external_id="1")
    ])
    payload = client.post("/api/library/fill", json={"sources": ["netease"]}).json()
    assert payload["filled"] == 1
    assert path.exists()


# --------------------------------------------------------------------- 元数据服务


def test_metadata_service_exposes_each_platform() -> None:
    settings = load_settings()
    service = MetadataService(settings, Store(settings.database_path))

    async def scenario() -> None:
        await service.startup()
        try:
            assert set(service.providers) >= {"musicbrainz", "netease", "qq", "kugou", "kuwo"}
        finally:
            await service.shutdown()

    asyncio.run(scenario())


# ------------------------------------------------------------- 从音源取词（只读）


class FakeLyricsProvider(FakeProvider):
    """能按 id 取词的替身：歌词正文由用例给定，取不到时给出具体原因。"""

    supports_lyrics = True
    lyrics_note = ""
    search_note = ""

    def __init__(self, candidates: list[TrackMetadata], text: str | None = None,
                 reason: str = "") -> None:
        super().__init__(candidates)
        self.text = text
        self.reason = reason
        self.lyric_calls: list[str] = []

    async def lyrics(self, song_id: str, *, album: str | None = None) -> Any:
        from musicpilot.adapters.metadata.base import LyricsResult

        self.lyric_calls.append(song_id)
        return LyricsResult(text=self.text, reason=self.reason)


class NoLyricsProvider(FakeProvider):
    """像 MusicBrainz 那样：能搜，但根本不提供歌词。"""

    supports_lyrics = False
    lyrics_note = "MusicBrainz 不提供歌词正文"
    search_note = ""


def test_lyrics_fetch_returns_the_real_lyrics_text(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "t.mp3", {"title": "晴天", "artist": "周杰伦"})
    before = _snapshot(workspace["library"])
    context = _client_context(client)
    provider = FakeLyricsProvider(
        [TrackMetadata(title="晴天", artist="周杰伦", source="netease", external_id="186016")],
        text="[00:01.00]故事的小黄花\n[00:02.00]从出生那年就飘着",
    )
    context.metadata.providers["netease"] = provider

    payload = client.post(
        "/api/library/lyrics/fetch",
        json={"source": "netease", "external_id": "186016", "album": "叶惠美"},
    ).json()
    assert payload["lyrics"] == "[00:01.00]故事的小黄花\n[00:02.00]从出生那年就飘着"
    assert payload["reason"] == ""
    assert payload["source"] == "netease"
    assert provider.lyric_calls == ["186016"]
    # 只读端点：一个字都不许写盘
    assert _snapshot(workspace["library"]) == before
    assert tag_value(path, "title") == "晴天"


def test_lyrics_fetch_reports_unsupported_source(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    make_mp3(workspace["library"] / "t.mp3", {"title": "歌"})
    _client_context(client).metadata.providers["musicbrainz"] = NoLyricsProvider([])
    payload = client.post(
        "/api/library/lyrics/fetch", json={"source": "musicbrainz", "external_id": "mbid-1"}
    ).json()
    assert payload["lyrics"] is None
    assert payload["reason"] == REASON_UNSUPPORTED


def test_lyrics_fetch_reports_no_response_and_no_lyrics_separately(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    make_mp3(workspace["library"] / "t.mp3", {"title": "歌"})
    context = _client_context(client)
    context.metadata.providers["qq"] = FakeLyricsProvider([], reason=REASON_NO_RESPONSE)
    context.metadata.providers["kugou"] = FakeLyricsProvider([], reason=REASON_NO_LYRICS)

    broken = client.post(
        "/api/library/lyrics/fetch", json={"source": "qq", "external_id": "mid"}
    ).json()
    empty = client.post(
        "/api/library/lyrics/fetch", json={"source": "kugou", "external_id": "hash"}
    ).json()
    assert broken["lyrics"] is None and broken["reason"] == REASON_NO_RESPONSE
    assert empty["lyrics"] is None and empty["reason"] == REASON_NO_LYRICS


def test_lyrics_fetch_rejects_unknown_source(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    make_mp3(workspace["library"] / "t.mp3", {"title": "歌"})
    response = client.post(
        "/api/library/lyrics/fetch", json={"source": "piracy-bay", "external_id": "1"}
    )
    assert response.status_code == 400


def test_sources_payload_exposes_availability_and_notes(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    make_mp3(workspace["library"] / "t.mp3", {"title": "歌"})
    context = _client_context(client)
    context.metadata.providers["musicbrainz"] = NoLyricsProvider([])
    context.metadata.providers["netease"] = FakeLyricsProvider([])
    payload = client.get("/api/library").json()
    by_id = {item["id"]: item for item in payload["sources"]}
    assert set(by_id) == {"musicbrainz", "netease", "qq", "kugou", "kuwo"}
    assert by_id["netease"]["available"] is True and by_id["netease"]["lyrics"] is True
    # 不能取词的源必须如实标出来，前端才好置灰
    assert by_id["musicbrainz"]["lyrics"] is False
    assert "歌词" in by_id["musicbrainz"]["lyrics_note"]
    assert by_id["musicbrainz"]["note"]


def test_apply_reports_lyrics_reason_instead_of_pretending_none(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "t.mp3", {"title": "歌", "artist": "歌手"})
    _enable_tag_writing(client)
    _client_context(client).metadata.providers["netease"] = FakeLyricsProvider(
        [], reason=REASON_NO_RESPONSE
    )
    payload = client.post(
        "/api/library/apply",
        json={
            "path": str(path),
            "metadata": {"title": "歌", "artist": "歌手", "source": "netease", "external_id": "1"},
            "fetch_lyrics": True,
        },
    ).json()
    assert payload["lyrics_reason"] == REASON_NO_RESPONSE
    assert "lyrics" not in (payload["written"] or [])


def test_search_reports_per_source_stats(client: TestClient, workspace: dict[str, Any]) -> None:
    """逐源交代：搜到几条 / 这次有没有搜成，界面才分得开「0 条」与「接口挂」。"""
    path = make_mp3(workspace["library"] / "t.mp3", {"title": "歌"})
    context = _client_context(client)
    context.metadata.providers["netease"] = FakeLyricsProvider(
        [TrackMetadata(title="歌", source="netease", external_id="1")]
    )

    class BrokenProvider(FakeProvider):
        supports_lyrics = True

        async def search(self, **kwargs: Any) -> list[TrackMetadata]:
            raise RuntimeError("接口没回来")

    context.metadata.providers["qq"] = BrokenProvider([])
    assert client.put(
        "/api/settings", json={"values": {"metadata_sources": ["local", "netease", "qq"]}}
    ).status_code == 200

    payload = client.post("/api/library/search", json={"path": str(path), "source": ""}).json()
    stats = {row["id"]: row for row in payload["source_stats"]}
    assert stats["netease"]["ok"] is True and stats["netease"]["count"] == 1
    assert stats["qq"]["ok"] is False and stats["qq"]["count"] == 0
    assert stats["qq"]["note"] == "接口无返回"


def test_search_marks_candidates_fetchable_only_when_source_supports_lyrics(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    path = make_mp3(workspace["library"] / "t.mp3", {"title": "歌"})
    context = _client_context(client)
    # 两首同名会让去重把它们合成一条，所以用两个明显不同的候选
    context.metadata.providers["kugou"] = FakeLyricsProvider(
        [TrackMetadata(title="有词的歌", artist="甲", source="kugou", external_id="h")]
    )
    context.metadata.providers["musicbrainz"] = NoLyricsProvider(
        [TrackMetadata(title="没词的歌", artist="乙", source="musicbrainz", external_id="mb")]
    )
    assert client.put(
        "/api/settings",
        json={"values": {"metadata_sources": ["local", "kugou", "musicbrainz"]}},
    ).status_code == 200
    payload = client.post(
        "/api/library/search",
        json={"path": str(path), "source": "", "title": "歌", "limit": 10},
    ).json()
    flags = {
        item["metadata"]["source"]: item["can_fetch_lyrics"] for item in payload["results"]
    }
    assert flags["kugou"] is True
    assert flags["musicbrainz"] is False
