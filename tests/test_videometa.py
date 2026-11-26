"""轻量视频时长解析测试：MP4 mvhd / MKV EBML Info / 不支持容器。"""

import struct

from wushuang.music.videometa import video_duration


def _box(btype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), btype) + payload


def _mvhd(timescale: int, duration: int) -> bytes:
    # version 0：flags(3) + creation(4) + modification(4) + timescale(4) + duration(4)
    payload = b"\x00" * 4 + b"\x00" * 8 + struct.pack(">II", timescale, duration)
    return _box(b"mvhd", payload)


def _mp4(moov_at_end: bool = False) -> bytes:
    ftyp = _box(b"ftyp", b"isom" + b"\x00" * 4)
    moov = _box(b"moov", _mvhd(1000, 12345))
    mdat = _box(b"mdat", b"\x00" * 64)
    return ftyp + mdat + moov if moov_at_end else ftyp + moov + mdat


def test_mp4_duration(tmp_path):
    p = tmp_path / "a.mp4"
    p.write_bytes(_mp4())
    assert abs(video_duration(p) - 12.345) < 1e-6


def test_mp4_moov_at_end(tmp_path):
    p = tmp_path / "b.m4v"
    p.write_bytes(_mp4(moov_at_end=True))
    assert abs(video_duration(p) - 12.345) < 1e-6


def _vint_size(n: int) -> bytes:
    assert n < 0x7F
    return bytes([0x80 | n])


def _elem(eid: bytes, payload: bytes) -> bytes:
    return eid + _vint_size(len(payload)) + payload


def _mkv(duration: float, scale: int = 1_000_000) -> bytes:
    ebml_header = _elem(b"\x1a\x45\xdf\xa3", b"")
    info = _elem(b"\x2a\xd7\xb1", struct.pack(">I", scale))
    info += _elem(b"\x44\x89", struct.pack(">f", duration))
    segment = _elem(b"\x18\x53\x80\x67", _elem(b"\x15\x49\xa9\x66", info))
    return ebml_header + segment


def test_mkv_duration(tmp_path):
    p = tmp_path / "a.mkv"
    # TimecodeScale=1ms，Duration=5000 → 5 秒
    p.write_bytes(_mkv(5000.0))
    assert abs(video_duration(p) - 5.0) < 1e-4


def test_mkv_custom_timescale(tmp_path):
    p = tmp_path / "b.webm"
    # TimecodeScale=2ms，Duration=2500 → 5 秒
    p.write_bytes(_mkv(2500.0, scale=2_000_000))
    assert abs(video_duration(p) - 5.0) < 1e-4


def test_unsupported_and_garbage(tmp_path):
    avi = tmp_path / "x.avi"
    avi.write_bytes(b"RIFFxxxxAVI ")
    assert video_duration(avi) == 0.0
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"\x00\x01\x02")
    assert video_duration(bad) == 0.0
    assert video_duration(tmp_path / "missing.mp4") == 0.0