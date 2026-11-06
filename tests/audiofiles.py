"""生成「真能读、真能写标签」的最小音频文件，供音乐库测试使用。

假的字节串（b"fake-audio-payload"）能被扫描出来，但 mutagen 打不开，也就写不进
标签——音乐库补全必须有可写标签的文件才测得准，所以这里拼一个合法的 MPEG-1
Layer III 帧（静音，128kbps / 44.1kHz / 单声道），前面挂一个空的 ID3v2 头。
"""

from __future__ import annotations

import zlib
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


# --- 其它格式的封面夹具：封面在属性上（FLAC）或在标签里（MP4 / Vorbis）-------------
# 都用 mutagen 自己的写入路径生成，保证「写法」与线上生产代码一致：
# FLAC 走 ``audio.add_picture()`` → ``audio.pictures`` 属性，
# Vorbis 走 ``metadata_block_picture`` → base64 的 FLAC Picture 块，
# MP4 走 ``covr`` → ``MP4Cover``。

PNG_COVER = b"\x89PNG\r\n\x1a\n" + b"cover-payload" * 4


# 一个最小但合法的 FLAC 流：44.1kHz / 16bit / 单声道，总采样数写死为 160。
# ``MUSICPI_<十六进制长度><16bit CRC>`` 的元数据块就是注释块（Vorbis comment），
# mutagen 会把它解析成 ``audio.tags``；没有注释块时 mutagen 直接认为文件损坏。
_FLAC_STREAMINFO = bytes.fromhex(
    "1000100000000010001000000000a00000000000000000000000000000000000"
)  # 占位，下面按 mutagen 的写法重算
_FLAC_TOTAL_SAMPLES = 160
_FLAC_TAIL = b"\x00" * 64


def _flac_header() -> bytes:
    min_block = max_block = 4096
    info = bytearray(34)
    info[0:2] = min_block.to_bytes(2, "big")
    info[2:4] = max_block.to_bytes(2, "big")
    info[4:7] = b"\x00\x00\x00"  # min frame size
    info[7:10] = b"\x00\x00\x00"  # max frame size
    packed = (44100 << 44) | ((1 - 1) << 41) | ((16 - 1) << 36) | _FLAC_TOTAL_SAMPLES
    info[10:18] = packed.to_bytes(8, "big")
    return b"fLaC" + _block(0, bytes(info))


def _block(kind: int, payload: bytes, *, last: bool = False) -> bytes:
    head = (0x80 if last else 0x00) | kind
    return bytes([head]) + len(payload).to_bytes(3, "big") + payload


def _flac_comment_block(last: bool = True) -> bytes:
    # Vorbis comment：厂商串 + 0 个字段，最小合法结构；后面由 mutagen 追加字段
    vendor = b"musicpilot-tests"
    payload = len(vendor).to_bytes(4, "little") + vendor + (0).to_bytes(4, "little")
    return _block(4, payload, last=last)


def _flac_bytes() -> bytes:
    return _flac_header() + _flac_comment_block() + _FLAC_TAIL


def make_flac(path: Path, tags: dict[str, str] | None = None, *, cover: bool = False) -> Path:
    """写一个 mutagen 认得的 FLAC（注释块 + 静音帧），``cover=True`` 时内嵌 PNG 封面。"""
    from mutagen.flac import FLAC, Picture

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_flac_bytes())
    audio = FLAC(str(path))
    audio["title"] = [(tags or {}).get("title", "Flac Track")]
    if tags and tags.get("artist"):
        audio["artist"] = [tags["artist"]]
    if cover:
        picture = Picture()
        picture.type = 3
        picture.mime = "image/png"
        picture.desc = "Cover"
        picture.width = 1
        picture.height = 1
        picture.depth = 24
        picture.data = PNG_COVER
        audio.add_picture(picture)
    audio.save()
    return path


def _ogg_page(serial: int, seq: int, packet: bytes, *, header_type: int, granule: int) -> bytes:
    """拼一个合法的 Ogg 页（单包、单段表项，包长 < 255 字节）。"""
    assert len(packet) < 255
    table = bytes([len(packet)])
    crc_input = (
        b"OggS" + bytes([0, header_type])
        + granule.to_bytes(8, "little")
        + serial.to_bytes(4, "little")
        + seq.to_bytes(4, "little")
        + b"\x00\x00\x00\x00"
        + bytes([1]) + table + packet
    )
    crc = zlib.crc32(crc_input) & 0xFFFFFFFF
    return (
        b"OggS" + bytes([0, header_type])
        + granule.to_bytes(8, "little")
        + serial.to_bytes(4, "little")
        + seq.to_bytes(4, "little")
        + crc.to_bytes(4, "little")
        + bytes([1]) + table + packet
    )


def make_ogg(path: Path, tags: dict[str, str] | None = None, *, cover: bool = False) -> Path:
    """写一个最小 Ogg Vorbis（识别头 + 注释头 + 一个 setup 包）。

    ``cover=True`` 时把 ``metadata_block_picture``（base64 的 FLAC Picture 块）
    写进注释头——就是生产代码写 Vorbis 封面的那条路径。
    """
    import base64

    from mutagen.flac import Picture

    path.parent.mkdir(parents=True, exist_ok=True)
    identify = b"\x01vorbis" + bytes(4) + bytes([1]) + (44100).to_bytes(4, "little")
    identify += bytes(4) + bytes(4) + bytes(4) + bytes([0xB8]) + bytes(1)
    comments = [("title", (tags or {}).get("title", "Ogg Track"))]
    if tags and tags.get("artist"):
        comments.append(("artist", tags["artist"]))
    if cover:
        picture = Picture()
        picture.type = 3
        picture.mime = "image/png"
        picture.desc = "Cover"
        picture.data = PNG_COVER
        comments.append(
            ("metadata_block_picture", base64.b64encode(picture.write()).decode("ascii"))
        )
    # mutagen 读标签时把包头的 7 字节（``\x03vorbis``）切掉，所以这里一定得写
    vendor = b"musicpilot-tests"
    comment_packet = (
        b"\x03vorbis"
        + len(vendor).to_bytes(4, "little") + vendor
        + len(comments).to_bytes(4, "little")
        + b"".join(
            len(item).to_bytes(4, "little") + item
            for item in (f"{key}={value}".encode() for key, value in comments)
        )
        + b"\x01"  # framing bit
    )
    path.write_bytes(
        _ogg_page(1, 0, identify, header_type=2, granule=0)
        + _ogg_page(1, 1, comment_packet, header_type=0, granule=0)
        # 后面的包 mutagen 只在写标签时搬移，不解析
        + _ogg_page(1, 2, b"\x05vorbis" + b"\x00" * 24, header_type=4, granule=0)
        + _ogg_page(1, 3, b"\x00" * 16, header_type=4, granule=160)
    )
    return path


# 最小可解析的 MP4 骨架：ftyp + moov（mvhd / 一条 soun 轨道的 trak），
# 有它 mutagen 才认得出这是 MP4，之后就能照常 ``add_tags()`` / ``save()``。
def _atom(kind: bytes, payload: bytes = b"") -> bytes:
    return (8 + len(payload)).to_bytes(4, "big") + kind + payload


def _full_atom(kind: bytes, version: int, flags: int, payload: bytes = b"") -> bytes:
    return _atom(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _mp4_skeleton() -> bytes:
    import struct

    timescale = duration = 44100  # 1 秒
    ftyp = _atom(b"ftyp", b"M4A " + (512).to_bytes(4, "big") + b"M4A mp42isom")
    mvhd = _full_atom(
        b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, duration) + bytes(80)
    )
    tkhd = _full_atom(
        b"tkhd", 0, 7, struct.pack(">IIIII", 0, 0, 1, 0, duration) + bytes(60)
    )
    mdhd = _full_atom(
        b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, duration) + bytes(4)
    )
    hdlr = _full_atom(b"hdlr", 0, 0, bytes(4) + b"soun" + bytes(12) + bytes(1))
    smhd = _full_atom(b"smhd", 0, 0, bytes(4))
    dref = _full_atom(b"dref", 0, 0, struct.pack(">I", 0))
    stsd = _full_atom(b"stsd", 0, 0, struct.pack(">I", 0))
    minf = _atom(b"minf", smhd + _atom(b"dinf", dref) + _atom(b"stbl", stsd))
    mdia = _atom(b"mdia", mdhd + hdlr + minf)
    return ftyp + _atom(b"moov", mvhd + _atom(b"trak", tkhd + mdia))


def make_m4a(path: Path, tags: dict[str, str] | None = None, *, cover: bool = False) -> Path:
    """写一个最小 MP4/M4A；``cover=True`` 时写 ``covr``。"""
    from mutagen.mp4 import MP4, MP4Cover

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_mp4_skeleton())
    audio = MP4(str(path))
    audio.add_tags()
    audio["\xa9nam"] = [(tags or {}).get("title", "M4a Track")]
    if cover:
        audio["covr"] = [MP4Cover(PNG_COVER, imageformat=MP4Cover.FORMAT_PNG)]
    audio.save()
    return path
