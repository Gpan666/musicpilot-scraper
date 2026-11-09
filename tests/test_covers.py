"""封面刮削：候选要带真图、代理要认得出图、补全要真内嵌。

这一组用例对应的是「音乐源封面刮削能力不行」的三个断点：
1. 五个音源里有三个候选根本没带封面地址；
2. 图床回的 Content-Type 五花八门，代理把它们当「不是图」丢了；
3. 音乐库补全只写文本标签，界面上写着封面其实没落盘。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from musicpilot.adapters.metadata.kugou import KugouProvider
from musicpilot.adapters.metadata.kuwo import KuwoProvider
from musicpilot.adapters.metadata.netease import NetEaseProvider
from musicpilot.adapters.scanner import embedded_cover
from musicpilot.adapters.tags import fetch_cover
from musicpilot.api.routes_cover import _allowed
from musicpilot.domain.library import plan_fill
from musicpilot.domain.models import TrackMetadata
from tests.audiofiles import make_mp3

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ 音源：候选带封面


def test_kugou_search_fills_cover_from_album_info() -> None:
    """song_search_v2 的 AlbumImage 是空的，只能靠 AlbumID 去 album/info 取真图。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "album/info" in str(request.url):
            assert "albumid=966846" in str(request.url)
            return httpx.Response(
                200,
                json={"data": {"albumname": "叶惠美",
                               "imgurl": "http://imge.kugou.com/stdmusic/{size}/x.jpg"}},
            )
        return httpx.Response(
            200,
            json={"data": {"lists": [
                {"FileHash": "H", "SongName": "晴天", "SingerName": "周杰伦",
                 "AlbumName": "叶惠美", "AlbumID": "966846", "AlbumImage": ""},
            ]}},
        )

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await KugouProvider(client).search(title="晴天")

    candidates = asyncio.run(scenario())
    # {size} 必须换掉，不然下载回来是坏的
    assert candidates[0].cover_url == "http://imge.kugou.com/stdmusic/480/x.jpg"


def test_kugou_never_uses_the_singer_avatar_as_album_cover() -> None:
    """Image 字段是歌手头像，拿它当专辑封面等于骗人：宁可留空。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "album/info" in str(request.url):
            return httpx.Response(200, json={"data": {}})
        return httpx.Response(
            200,
            json={"data": {"lists": [
                {"FileHash": "H", "SongName": "晴天", "SingerName": "周杰伦",
                 "AlbumID": "1", "Image": "http://singerimg.kugou.com/a/{size}.jpg"},
            ]}},
        )

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await KugouProvider(client).search(title="晴天")

    assert asyncio.run(scenario())[0].cover_url is None


def test_netease_search_fills_cover_from_detail() -> None:
    """搜索接口只给 picId 不给 picUrl，封面得去 /api/song/detail 补。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if "song/detail" in str(request.url):
            return httpx.Response(
                200,
                json={"songs": [{"id": 186016, "album": {
                    "picUrl": "https://p2.music.126.net/abc==/109951165566379710.jpg"}}]},
            )
        return httpx.Response(
            200,
            json={"result": {"songs": [{
                "id": 186016, "name": "晴天", "artists": [{"name": "周杰伦"}],
                "album": {"name": "叶惠美", "picId": 109951165566379710},
            }]}},
        )

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await NetEaseProvider(client).search(title="晴天")

    cover = asyncio.run(scenario())[0].cover_url
    assert cover is not None
    assert "p2.music.126.net" in cover and cover.endswith("?param=500y500")


def test_kuwo_cover_asks_for_the_big_picture() -> None:
    """web_albumpic_short 前缀就是尺寸，原样用只有 120×120。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=("{'abslist':[{'DC_TARGETID':'1','SONGNAME':'夜曲','ARTIST':'周杰伦',"
                  "'FARTIST':'周杰伦','ALBUM':'十一月的萧邦',"
                  "'web_albumpic_short':'120/s3s94/93/211513640.jpg'}]}"),
        )

    async def scenario() -> list[TrackMetadata]:
        async with _client(handler) as client:
            return await KuwoProvider(client).search(title="夜曲")

    assert asyncio.run(scenario())[0].cover_url == (
        "https://img1.kuwo.cn/star/albumcover/500/s3s94/93/211513640.jpg"
    )


# ------------------------------------------------------------------ 代理：认得出真图


def _fetch(url: str, answer: httpx.Response) -> tuple[bytes, str] | None:
    def handler(request: httpx.Request) -> httpx.Response:
        return answer

    async def scenario() -> tuple[bytes, str] | None:
        async with _client(handler) as client:
            return await fetch_cover(url, client=client)

    return asyncio.run(scenario())


@pytest.mark.parametrize(
    ("declared", "data", "expected"),
    [
        ("image/jpg", JPEG, "image/jpeg"),  # 网易云图床就爱回这个
        ("application/octet-stream", JPEG, "image/jpeg"),
        ("text/plain", PNG, "image/png"),
        ("image/jpeg; charset=binary", JPEG, "image/jpeg"),
    ],
)
def test_fetch_cover_trusts_the_file_header(
    declared: str, data: bytes, expected: str
) -> None:
    answer = httpx.Response(200, content=data, headers={"content-type": declared})
    payload = _fetch("https://p1.music.126.net/a.jpg", answer)
    assert payload is not None
    assert payload[1] == expected


def test_fetch_cover_rejects_html_error_pages() -> None:
    answer = httpx.Response(
        200, content=b"<html>404</html>", headers={"content-type": "text/html"}
    )
    assert _fetch("https://p1.music.126.net/a.jpg", answer) is None


def test_cover_proxy_allows_every_artwork_host_the_providers_use() -> None:
    for url in (
        "https://p1.music.126.net/abc==/1.jpg?param=500y500",
        "https://p2.music.126.net/abc==/1.jpg",
        "https://imge.kugou.com/stdmusic/480/1.jpg",
        "https://img1.kuwo.cn/star/albumcover/500/1.jpg",
        "https://y.gtimg.cn/music/photo_new/T002R500x500M000.jpg",
        "https://coverartarchive.org/release/x/front-250",
    ):
        assert _allowed(url), url
    assert not _allowed("https://example.com/evil.jpg")


# ------------------------------------------------------------------ 补全：封面真落盘


def test_plan_fill_reports_the_cover_it_will_embed() -> None:
    local = TrackMetadata(title="t")
    remote = TrackMetadata(title="t", cover_url="https://p1.music.126.net/a.jpg")
    assert plan_fill(local, remote)["cover"] == remote.cover_url


def test_plan_fill_never_overwrites_an_existing_cover() -> None:
    local = TrackMetadata(title="t", cover_url="https://old/1.jpg")
    remote = TrackMetadata(title="t", cover_url="https://new/2.jpg")
    assert "cover" not in plan_fill(local, remote)


def test_apply_embeds_the_candidate_cover(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    """候选有封面、文件里没有：写入之后文件里真的有一张图。"""
    client.put("/api/settings", json={"values": {"write_tags": True}})
    target = workspace["library"] / "track.mp3"
    make_mp3(target, {"title": "晴天"})

    context = client.app.state.context  # type: ignore[attr-defined]

    async def fake_cover(url: str | None) -> tuple[bytes, str]:
        assert url == "https://p1.music.126.net/a.jpg"
        return JPEG, "image/jpeg"

    context.metadata.cover = fake_cover
    response = client.post(
        "/api/library/apply",
        json={
            "path": str(target),
            "metadata": {"title": "晴天", "cover_url": "https://p1.music.126.net/a.jpg"},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "cover" in body["written"]
    assert embedded_cover(Path(target)) is not None


def test_apply_reports_a_failed_cover_instead_of_pretending(
    client: TestClient, workspace: dict[str, Any]
) -> None:
    client.put("/api/settings", json={"values": {"write_tags": True}})
    target = workspace["library"] / "track.mp3"
    make_mp3(target, {"title": "晴天"})

    context = client.app.state.context  # type: ignore[attr-defined]

    async def broken(url: str | None) -> None:
        return None

    context.metadata.cover = broken
    body = client.post(
        "/api/library/apply",
        json={
            "path": str(target),
            "metadata": {"title": "晴天", "cover_url": "https://p1.music.126.net/a.jpg"},
        },
    ).json()
    assert "cover" not in body["written"]
    assert "封面" in body["cover_reason"]
    assert embedded_cover(Path(target)) is None
