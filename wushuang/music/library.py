"""本地曲库：扫描 NAS 音乐目录、读取标签、维护曲目索引。"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import mutagen

from ..storage import Storage
from ..utils.text import sanitize_filename

_LOGGER = logging.getLogger(__name__)

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".ogg", ".opus", ".aac", ".wma"}


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
    netease_root = music_dir / "netease"
    playlists = {
        (p.get("folder") or ""): p["id"]
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
            for folder, pid in playlists.items():
                if folder and str(path).startswith(folder.rstrip("/\\") + os.sep):
                    playlist_id = pid
                    source = "netease" if str(path).startswith(str(netease_root)) else "local"
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
    # 清理已删除文件的索引
    removed = 0
    async with storage.db.execute("SELECT id,path FROM tracks") as cur:
        rows = [dict(r) async for r in cur]
    for row in rows:
        if row["path"] not in seen_paths:
            await storage.delete_track(row["id"])
            removed += 1
    return {"scanned": len(paths), "removed": removed}


def netease_playlist_folder(music_dir: Path, playlist_name: str) -> Path:
    return music_dir / "netease" / sanitize_filename(playlist_name)
