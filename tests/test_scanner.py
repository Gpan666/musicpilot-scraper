"""Scanner robustness against mutagen's inconsistent tag backends.

mutagen's Vorbis-family tags (FLAC/Ogg/Opus) raise ``ValueError`` on a *missing*
key instead of returning ``None`` like the MP3/ID3 backend. A scan must survive
that and keep going.
"""

from pathlib import Path

from musicpilot.adapters.scanner import _safe_get, read_tags


class _FakeVorbis:
    """Mimic mutagen's Vorbis backend: ``.get()`` raises ValueError for missing keys."""

    def get(self, key, default=None):  # noqa: ARG002 - mimic mutagen behaviour
        raise ValueError

    @property
    def info(self):
        class _Info:
            length = 180.0
            bitrate = 320000

        return _Info()

    def close(self):
        return None


def test_safe_get_catches_vorbis_valueerror() -> None:
    fake = _FakeVorbis()
    assert _safe_get(fake, "date") is None
    assert _safe_get(fake, "APIC") is None


def test_read_tags_survives_vorbis_valueerror(monkeypatch) -> None:
    monkeypatch.setattr(
        "musicpilot.adapters.scanner.MutagenFile", lambda *a, **k: _FakeVorbis()
    )
    metadata, has_cover, technical = read_tags(Path("/tmp/does-not-matter.ogg"))
    assert metadata is not None
    assert metadata.title == ""
    assert has_cover is False
    assert technical["duration"] == 180.0


# --- 内嵌封面判定的回归：ID3 的封面帧键名是 ``APIC:<描述>`` ---------------------


def _mp3_with_apic(tmp_path: Path, desc: str) -> Path:
    """写一个带 ``APIC:<desc>`` 内嵌封面的 mp3（mutagen 存成 ``APIC:`` 或 ``APIC:cover``）。"""
    from mutagen.id3 import APIC, ID3

    from tests.audiofiles import make_mp3

    path = make_mp3(tmp_path / "with-cover.mp3", {"title": "Covered"})
    tags = ID3(str(path))
    tags.add(
        APIC(encoding=3, mime="image/png", type=3, desc=desc, data=b"\x89PNG" + b"0" * 32)
    )
    tags.save(str(path))
    return path


def test_read_tags_detects_apic_frame_with_empty_description(
    tmp_path: Path, monkeypatch
) -> None:
    from musicpilot.adapters.scanner import MutagenFile

    path = _mp3_with_apic(tmp_path, "")
    # 前提：mutagen 把空描述的封面帧存成 ``APIC:``，精确取 "APIC" 取不到
    assert "APIC:" in list(MutagenFile(str(path)).tags.keys())

    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True


def test_read_tags_detects_apic_frame_with_description(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover

    path = _mp3_with_apic(tmp_path, "cover")
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True
    # 判「有封面」与取封面口径一致
    cover = embedded_cover(path)
    assert cover is not None and cover[0].startswith(b"\x89PNG")


def test_read_tags_without_cover_stays_false(tmp_path: Path) -> None:
    from tests.audiofiles import make_mp3

    path = make_mp3(tmp_path / "no-cover.mp3", {"title": "Bare"})
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is False


# --- FLAC 回归：封面在 ``audio.pictures`` 属性上，不在标签字典里 -----------------
# #43 把判封面换成只查标签键（``_COVER_KEYS`` 里写着 "pictures"，可 FLAC 没有这个键），
# 于是 154 首库里 FLAC 的内嵌封面几乎全被判成「没有封面」，取封面也一起失效。


def test_read_tags_detects_flac_pictures_attribute(tmp_path: Path) -> None:
    from tests.audiofiles import make_flac

    path = make_flac(tmp_path / "with-cover.flac", {"title": "Flac"}, cover=True)
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True


def test_embedded_cover_reads_flac_pictures_attribute(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover
    from tests.audiofiles import PNG_COVER, make_flac

    path = make_flac(tmp_path / "with-cover.flac", {"title": "Flac"}, cover=True)
    cover = embedded_cover(path)
    assert cover is not None
    data, mime = cover
    assert data == PNG_COVER
    assert mime == "image/png"


def test_cover_entry_and_extraction_agree_on_flac(tmp_path: Path) -> None:
    """判封面与取封面同一个入口：判「有」就必须取得到字节，反之亦然。"""
    from musicpilot.adapters.scanner import MutagenFile, _cover_entry, embedded_cover
    from tests.audiofiles import make_flac

    for cover, expected in ((True, True), (False, False)):
        path = make_flac(
            tmp_path / f"flac-{cover}.flac", {"title": "Flac"}, cover=cover
        )
        audio = MutagenFile(str(path))
        assert (_cover_entry(audio) is not None) is expected
        assert (embedded_cover(path) is not None) is expected


def test_flac_without_cover_stays_false(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover
    from tests.audiofiles import make_flac

    path = make_flac(tmp_path / "bare.flac", {"title": "Bare"})
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is False
    assert embedded_cover(path) is None


# --- 其它格式别被误伤：mp4/m4a 的 covr、vorbis 的 metadata_block_picture ---------


def test_read_tags_detects_m4a_covr(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover
    from tests.audiofiles import make_m4a

    path = make_m4a(tmp_path / "with-cover.m4a", {"title": "M4a"}, cover=True)
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True
    cover = embedded_cover(path)
    assert cover is not None and cover[0]


def test_read_tags_detects_ogg_metadata_block_picture(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover
    from tests.audiofiles import make_ogg

    path = make_ogg(tmp_path / "with-cover.ogg", {"title": "Ogg"}, cover=True)
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is True
    cover = embedded_cover(path)
    assert cover is not None and cover[0]


def test_vorbis_without_cover_stays_false(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover
    from tests.audiofiles import make_ogg

    path = make_ogg(tmp_path / "bare.ogg", {"title": "Bare"})
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is False
    assert embedded_cover(path) is None


def test_mp3_without_cover_stays_false_and_empty(tmp_path: Path) -> None:
    from musicpilot.adapters.scanner import embedded_cover
    from tests.audiofiles import make_mp3

    path = make_mp3(tmp_path / "bare.mp3", {"title": "Bare"})
    _metadata, has_cover, _technical = read_tags(path)
    assert has_cover is False
    assert embedded_cover(path) is None


# --- 前缀匹配只给 ID3 的 ``APIC:`` 用 -----------------------------------------


def test_cover_entry_does_not_prefix_match_non_id3_keys(monkeypatch, tmp_path: Path) -> None:
    """``covr`` / ``metadata_block_picture`` 没有 ``<键>:<后缀>`` 这种写法。

    这类「长得像」的键不该被当成封面：判有封面却取不出字节，两边口径就分叉了。
    """
    from musicpilot.adapters.scanner import _cover_entry

    class _Bogus:
        tags = {"covr:fake": ["not-a-picture"], "metadata_block_picture:x": "garbage"}

        def get(self, key, default=None):
            return self.tags.get(key, default)

    assert _cover_entry(_Bogus()) is None


def test_cover_entry_still_prefix_matches_apic() -> None:
    from musicpilot.adapters.scanner import _cover_entry

    class _Frame:
        data = b"\x89PNG payload"
        mime = "image/png"

    class _Mp3Like:
        tags = {"TIT2": ["x"], "APIC:Cover": [_Frame()]}

        def get(self, key, default=None):
            return self.tags.get(key, default)

    frame = _cover_entry(_Mp3Like())
    assert frame is not None and frame.data == b"\x89PNG payload"
