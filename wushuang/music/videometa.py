"""轻量视频时长解析（纯 Python，无 ffmpeg 依赖）。

桌面版支持把本地视频推给小爱音箱（带屏机型可显示画面，其余机型尽力播放）。
自动切歌需要真实时长：mutagen 不认视频容器，这里直接解析
- MP4/MOV/M4V 的 moov/mvhd box（timescale + duration）
- MKV/WebM 的 EBML Segment/Info（TimecodeScale + Duration）
解析失败返回 0，播放引擎会退回估算时长，不影响入库与播放。
"""

from __future__ import annotations

import struct
from pathlib import Path

_MP4_EXTS = {".mp4", ".m4v", ".mov"}
_MKV_EXTS = {".mkv", ".webm"}

_MOOV_READ_CAP = 32 * 1024 * 1024  # moov 最大读取字节数（防异常文件撑爆内存）
_MKV_SCAN_CAP = 8 * 1024 * 1024    # MKV 顺序扫描上限（Info 一般在文件头部）


def video_duration(path: str | Path) -> float:
    """返回视频时长（秒）；不支持/解析失败返回 0.0。"""
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext in _MP4_EXTS:
            return _mp4_duration(p)
        if ext in _MKV_EXTS:
            return _mkv_duration(p)
    except Exception:  # noqa: BLE001 - 坏文件不影响扫描
        return 0.0
    return 0.0


# ---------- MP4 / MOV ----------

def _boxes(buf: bytes, start: int, end: int):
    """迭代 [start, end) 内的 box：(type, payload_start, payload_end)。"""
    pos = start
    while pos + 8 <= end:
        size, btype = struct.unpack_from(">I4s", buf, pos)
        header = 8
        if size == 1:  # 64 位 largesize
            if pos + 16 > end:
                return
            size = struct.unpack_from(">Q", buf, pos + 8)[0]
            header = 16
        elif size == 0:  # 延伸到末尾
            size = end - pos
        if size < header or pos + size > end:
            return
        yield btype, pos + header, pos + size
        pos += size


def _mvhd_seconds(payload: bytes) -> float:
    if len(payload) < 4:
        return 0.0
    version = payload[0]
    if version == 1:
        if len(payload) < 32:
            return 0.0
        timescale, duration = struct.unpack_from(">IQ", payload, 20)
    else:
        if len(payload) < 20:
            return 0.0
        timescale, duration = struct.unpack_from(">II", payload, 12)
    return float(duration) / timescale if timescale else 0.0


def _mp4_duration(p: Path) -> float:
    with open(p, "rb") as f:
        # 顶层 box 只读头部逐个跳，找到 moov 再整体读入（可能在文件尾）
        pos = 0
        f.seek(0, 2)
        file_size = f.tell()
        while pos + 8 <= file_size:
            f.seek(pos)
            head = f.read(8)
            if len(head) < 8:
                break
            size, btype = struct.unpack(">I4s", head)
            header = 8
            if size == 1:
                ext = f.read(8)
                if len(ext) < 8:
                    break
                size = struct.unpack(">Q", ext)[0]
                header = 16
            elif size == 0:
                size = file_size - pos
            if size < header:
                break
            if btype == b"moov":
                length = min(size - header, _MOOV_READ_CAP)
                f.seek(pos + header)
                moov = f.read(length)
                for t, s, e in _boxes(moov, 0, len(moov)):
                    if t == b"mvhd":
                        return _mvhd_seconds(moov[s:e])
                return 0.0
            pos += size
    return 0.0


# ---------- MKV / WebM (EBML) ----------

def _vint(buf: bytes, pos: int, keep_marker: bool) -> tuple[int, int] | None:
    """读 EBML 变长整数，返回 (值, 新位置)；越界/非法返回 None。"""
    if pos >= len(buf):
        return None
    first = buf[pos]
    if first == 0:
        return None
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        mask >>= 1
        length += 1
    if length > 8 or pos + length > len(buf):
        return None
    value = first if keep_marker else first & (mask - 1)
    for i in range(1, length):
        value = (value << 8) | buf[pos + i]
    return value, pos + length


_EBML_SEGMENT = 0x18538067
_EBML_INFO = 0x1549A966
_EBML_TIMECODE_SCALE = 0x2AD7B1
_EBML_DURATION = 0x4489


def _mkv_duration(p: Path) -> float:
    with open(p, "rb") as f:
        buf = f.read(_MKV_SCAN_CAP)
    # 定位 Segment（顺序扫描顶层元素）
    pos = 0
    seg_start = seg_end = None
    while pos < len(buf):
        idr = _vint(buf, pos, keep_marker=True)
        if idr is None:
            break
        eid, pos = idr
        szr = _vint(buf, pos, keep_marker=False)
        if szr is None:
            break
        size, pos = szr
        if eid == _EBML_SEGMENT:
            seg_start = pos
            seg_end = min(pos + size, len(buf)) if size else len(buf)
            break
        pos += size  # size=0 为空 payload 元素（pos 已越过 ID/size 字段，不会死循环）
    if seg_start is None:
        return 0.0
    # 在 Segment 内找 Info（跳过 Cluster 等大元素时受读入上限约束）
    pos = seg_start
    while pos < seg_end:
        idr = _vint(buf, pos, keep_marker=True)
        if idr is None:
            break
        eid, pos = idr
        szr = _vint(buf, pos, keep_marker=False)
        if szr is None:
            break
        size, pos = szr
        if eid == _EBML_INFO:
            return _mkv_info_seconds(buf[pos:min(pos + size, seg_end)])
        if eid == 0x1F43B675:  # Cluster：媒体数据，Info 必在其前，停止
            break
        pos += size
    return 0.0


def _mkv_info_seconds(info: bytes) -> float:
    scale = 1_000_000  # 默认 TimecodeScale = 1ms（纳秒单位）
    duration = 0.0
    pos = 0
    while pos < len(info):
        idr = _vint(info, pos, keep_marker=True)
        if idr is None:
            break
        eid, pos = idr
        szr = _vint(info, pos, keep_marker=False)
        if szr is None:
            break
        size, pos = szr
        payload = info[pos:pos + size]
        if eid == _EBML_TIMECODE_SCALE and payload:
            scale = int.from_bytes(payload, "big") or scale
        elif eid == _EBML_DURATION and len(payload) in (4, 8):
            duration = struct.unpack(">f" if len(payload) == 4 else ">d", payload)[0]
        pos += size
    return float(duration) * scale / 1e9 if duration > 0 else 0.0