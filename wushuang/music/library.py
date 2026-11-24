"""本地曲库：扫描 NAS 音乐目录、读取标签、维护曲目索引。"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

import mutagen

from ..storage import Storage
from ..utils.text import sanitize_filename

_LOGGER = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".wma"}

_CUE_INDEX = re.compile(r"INDEX\s+01\s+(\d+):(\d+):(\d+)")
_CUE_TITLE = re.compile(r'^TITLE\s+"(.*)"\s*$')
_CUE_PERFORMER = re.compile(r'^PERFORMER\s+"(.*)"\s*$')
_CUE_TRACK = re.compile(r"^TRACK\s+(\d+)\s+AUDIO")


def parse_cue(cue_path: Path) -> tuple[list[dict], str, str]:
    """解析 CUE  sheet → (分轨列表[含起始帧], 专辑名, 专辑艺人)。"""
    tracks: list[dict] = []
    cur: dict | None = None
    album_title, album_artist = "", ""
    for line in cue_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        m = _CUE_TITLE.match(line)
        if m:
            if cur is None:
                album_title = m.group(1)
            else:
                cur["title"] = m.group(1)
            continue
        m = _CUE_PERFORMER.match(line)
        if m:
            if cur is None:
                album_artist = m.group(1)
            else:
                cur["artist"] = m.group(1)
            continue
        m = _CUE_TRACK.match(line)
        if m:
            cur = {"index": int(m.group(1)), "title": f"Track {m.group(1)}",
                   "artist": "", "start_frame": None}
            tracks.append(cur)
            continue
        m = _CUE_INDEX.search(line)
        if m and cur is not None and cur["start_frame"] is None:
            mm, ss, ff = map(int, m.groups())
            cur["start_frame"] = mm * 60 * 75 + ss * 75 + ff
    return ([t for t in tracks if t["start_frame"] is not None],
            album_title, album_artist)


def wav_data_offset(path: Path) -> tuple[int, int, int]:
    """RIFF 解析 → (data  chunk 偏移, 字节率, data 大小)，供 CUE 帧→字节换算。"""
    with open(path, "rb") as f:
        header = f.read(12)
        if header[:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise ValueError("not a WAVE file")
        byte_rate = data_offset = data_size = 0
        while True:
            chunk = f.read(8)
            if len(chunk) < 8:
                break
            cid = chunk[:4]
            size = int.from_bytes(chunk[4:8], "little")
            if cid == b"fmt ":
                fmt = f.read(size)
                byte_rate = int.from_bytes(fmt[8:12], "little")
            elif cid == b"data":
                data_offset, data_size = f.tell(), size
                break
            else:
                f.seek(size + (size & 1), 1)
    if not byte_rate or not data_offset:
        raise ValueError("WAVE fmt/data chunk missing")
    return data_offset, byte_rate, data_size


def _read_meta(path: Path) -> dict:
    title = path.stem
    artist = ""
    album = ""
    duration = 0.0
    try:
        meta = mutagen.File(path, easy=True)
        if meta is not None:
            title = (meta.get("title") or [path.stem])[0]
            artist = (meta.get("artist") or [""])[0]
            album = (meta.get("album") or [""])[0]
            if meta.info and getattr(meta.info, "length", None):
                duration = float(meta.info.length)
    except Exception:  # noqa: BLE001 - 坏标签不影响入库
        _LOGGER.debug("读取标签失败: %s", path, exc_info=True)
    return {
        "title": str(title),
        "artist": str(artist),
        "album": str(album),
        "duration": duration,
        "path": str(path),
    }


def _walk_paths(music_dir: Path) -> list[Path]:
    """只收集音频文件路径（快速，不读标签）。"""
    skip_dirs = {"#recycle", "@eaDir", "#snapshot", "@tmp"}
    paths = []
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fn in files:
            p = Path(root) / fn
            if p.suffix.lower() in AUDIO_EXTS:
                paths.append(p)
    return paths


def _scan_sync(music_dir: Path) -> list[dict]:
    return [_read_meta(p) for p in _walk_paths(music_dir)]


async def scan_library(storage: Storage, music_dir: Path) -> dict:
    """增量扫描：每批读标签并立即入库，中断不丢已完成进度。"""
    paths = await asyncio.to_thread(_walk_paths, music_dir)
    seen_paths: set[str] = set()
    playlists = {
        (p.get("folder") or ""): (p["id"], p.get("source") or "local")
        for p in await storage.list_playlists()
        if p.get("folder")
    }
    batch_size = 100
    for i in range(0, len(paths), batch_size):
        batch = paths[i:i + batch_size]
        metas = await asyncio.to_thread(lambda b=batch: [_read_meta(p) for p in b])
        for rec in metas:
            path = Path(rec["path"])
            # 有 CUE 的 WAV 整盘文件不入库（按分轨入库），旧整盘索引由清理移除
            if path.suffix.lower() == ".wav" and path.with_suffix(".cue").is_file():
                continue
            seen_paths.add(rec["path"])
            playlist_id = None
            source = "local"
            for folder, (pid, pl_source) in playlists.items():
                if folder and str(path).startswith(folder.rstrip("/\\") + os.sep):
                    playlist_id = pid
                    source = pl_source
                    break
            await storage.upsert_track(
                title=rec["title"],
                path=rec["path"],
                artist=rec["artist"],
                album=rec["album"],
                duration=rec["duration"],
                playlist_id=playlist_id,
                source=source,
            )
    # CUE 分轨：WAV CD 镜像按 .cue 拆成多曲目（虚拟路径 #cueN + 字节偏移）
    for p in paths:
        if p.suffix.lower() != ".wav":
            continue
        cue = p.with_suffix(".cue")
        if not cue.is_file():
            continue
        try:
            entries, album_title, album_artist = await asyncio.to_thread(parse_cue, cue)
            data_offset, byte_rate, data_size = await asyncio.to_thread(wav_data_offset, p)
        except Exception:  # noqa: BLE001 - 坏 cue/ wav 不影响整体扫描
            _LOGGER.debug("CUE 解析失败: %s", cue, exc_info=True)
            continue
        for i, e in enumerate(entries):
            start = data_offset + e["start_frame"] * byte_rate // 75
            end = (data_offset + entries[i + 1]["start_frame"] * byte_rate // 75
                   if i + 1 < len(entries) else data_offset + data_size)
            if end <= start:
                continue
            vpath = f"{p}#cue{e['index']}"
            seen_paths.add(vpath)
            await storage.upsert_track(
                title=e["title"], path=vpath,
                artist=e["artist"] or album_artist, album=album_title,
                duration=(end - start) / byte_rate, source="local",
                cue_start_byte=start, cue_end_byte=end,
            )
    # 清理已删除文件的索引
    removed = 0
    async with storage.db.execute("SELECT id,path FROM tracks") as cur:
        rows = [dict(r) async for r in cur]
    for row in rows:
        if row["path"] not in seen_paths:
            await storage.delete_track(row["id"])
            removed += 1
    return {"scanned": len(paths), "removed": removed}


def playlist_folder(music_dir: Path, source: str, playlist_name: str) -> Path:
    """远端歌单的本地目录：music_dir/<平台>/<歌单名>。"""
    return music_dir / source / sanitize_filename(playlist_name)


def resolve_local_folder(music_dir: Path, host_dir: str, raw: str) -> Path:
    """本地歌单目录：留空=新建子目录；否则指向已有目录。

    支持 NAS 宿主机路径（自动映射 host_dir 前缀到容器 music_dir）或容器路径；
    必须位于音乐库内，保证扫描与媒体流安全。
    """
    raw = (raw or "").strip()
    if not raw:
        return music_dir / "local"
    p = raw
    host = (host_dir or "").rstrip("/")
    if host and p.startswith(host):
        p = str(music_dir) + p[len(host):]
    path = Path(p)
    if not path.is_absolute():
        raise ValueError("目录必须是绝对路径")
    root = str(music_dir.resolve())
    if not str(path.resolve()).startswith(root):
        raise ValueError(f"目录需位于音乐目录内（{host_dir or music_dir}）")
    return path


def display_folder(music_dir: Path, host_dir: str, folder: str) -> str:
    """容器路径 → NAS 宿主机路径显示（统一 / 分隔符）。"""
    base = str(music_dir).replace("\\", "/")
    f = (folder or "").replace("\\", "/")
    if host_dir and f.startswith(base):
        return host_dir.rstrip("/") + f[len(base):]
    return folder
