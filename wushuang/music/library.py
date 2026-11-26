"""本地曲库：扫描 NAS 音乐目录、读取标签、维护曲目索引。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import mutagen

from ..storage import Storage
from ..utils.text import sanitize_filename
from .videometa import video_duration

_LOGGER = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".wma"}
# 桌面版额外收录视频（带屏小爱可显示画面；无屏机型尽力播放，取决于固件）
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".flv",
              ".ts", ".wmv", ".mpg", ".mpeg"}


def is_online_track(track: dict) -> bool:
    """在线音源：path 存的是 http(s) URL，不走本地媒体流。"""
    return str(track.get("path") or "").startswith(("http://", "https://"))


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
    if duration <= 0 and path.suffix.lower() in VIDEO_EXTS:
        duration = video_duration(path)  # mutagen 不认的容器用轻量解析兜底
    return {
        "title": str(title),
        "artist": str(artist),
        "album": str(album),
        "duration": duration,
        "path": str(path),
    }


def _walk_paths(music_dir: Path, include_video: bool = False) -> list[Path]:
    """收集媒体文件路径（快速，不读标签）；桌面版可含视频。"""
    skip_dirs = {"#recycle", "@eaDir", "#snapshot", "@tmp"}
    exts = AUDIO_EXTS | VIDEO_EXTS if include_video else AUDIO_EXTS
    paths = []
    for root, dirs, files in os.walk(music_dir):
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for fn in files:
            p = Path(root) / fn
            if p.suffix.lower() in exts:
                paths.append(p)
    return paths


def _scan_sync(music_dir: Path, include_video: bool = False) -> list[dict]:
    return [_read_meta(p) for p in _walk_paths(music_dir, include_video)]


async def scan_library(storage: Storage, music_dir: Path,
                       include_video: bool = False) -> dict:
    """增量扫描：每批读标签并立即入库，中断不丢已完成进度。

    在线音源（path 为 URL）不在磁盘上，清理失效索引时必须保留。
    """
    paths = await asyncio.to_thread(_walk_paths, music_dir, include_video)
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
    # 清理已删除文件的索引（在线音源除外）
    removed = 0
    async with storage.db.execute("SELECT id,path,source FROM tracks") as cur:
        rows = [dict(r) async for r in cur]
    for row in rows:
        if is_online_track(row):
            continue
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
