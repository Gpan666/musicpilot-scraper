"""生成「真能读、真能写标签」的最小音频文件，供音乐库测试使用。

假的字节串（b"fake-audio-payload"）能被扫描出来，但 mutagen 打不开，也就写不进
标签——音乐库补全必须有可写标签的文件才测得准，所以这里拼一个合法的 MPEG-1
Layer III 帧（静音，128kbps / 44.1kHz / 单声道），前面挂一个空的 ID3v2 头。
"""

from __future__ import annotations

from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK

_MP3_FRAME_HEADER = bytes([0xFF, 0xFB, 0x90, 0xC0])  # MPEG-1 L3, 128kbps, 44.1k, mono
_MP3_FRAME_SIZE = 144 * 128000 // 44100
_FRAME_COUNT = 40  # 约 2 秒


def make_mp3(path: Path, tags: dict[str, str | int] | None = None) -> Path:
    """写一个能被 mutagen 打开、并能继续写标签的 mp3。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (_MP3_FRAME_HEADER + b"\x00" * (_MP3_FRAME_SIZE - 4)) * _FRAME_COUNT
    path.write_bytes(payload)
    if tags:
        write_id3(path, tags)
    return path


def write_id3(path: Path, tags: dict[str, str | int]) -> None:
    frames: list[object] = []
    mapping = {
        "title": TIT2,
        "artist": TPE1,
        "album": TALB,
        "album_artist": TPE2,
        "year": TDRC,
        "track_number": TRCK,
    }
    for name, value in tags.items():
        factory = mapping.get(name)
        if factory is None or value in (None, ""):
            continue
        frames.append(factory(encoding=3, text=[str(value)]))
    existing = ID3()
    try:
        existing = ID3(str(path))
    except Exception:  # noqa: BLE001 - 文件还没有标签
        pass
    for frame in frames:
        existing.delall(frame.FrameID) if hasattr(existing, "delall") else None
        existing.add(frame)
    existing.save(str(path))


def read_easy(path: Path) -> dict[str, list[str]]:
    audio = MutagenFile(str(path), easy=True)
    assert audio is not None, f"mutagen cannot open {path}"
    return {key: list(value) for key, value in (audio.tags or {}).items()}


def tag_value(path: Path, key: str) -> str:
    values = read_easy(path).get(key) or []
    return str(values[0]) if values else ""
