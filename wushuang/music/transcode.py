"""高解析无损转码：判定 + 缓存 + 后台预转码。

小爱音箱（LX06/S12A 等）无法稳播高解析（PCM/DSD、采样率>48k、位深>16，
码率 5–9Mbps）。开启 media_transcode_hires 后，把这类文件用 ffmpeg 转成
44.1kHz/16bit MP3 缓存成文件，再以静态方式（FileResponse，带 Range）推流。

设计要点（避免卡顿）：
- 判定只用 size/时长 估算码率 + 扩展名，**不在每次播放请求里跑 ffprobe**；
- 转码结果按 track_id+源mtime+size 缓存，源文件变动自动失效；
- 后台预转码 worker 提前把高解析转好，播放时基本直接命中缓存（不阻塞）。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
from pathlib import Path

_LOGGER = logging.getLogger(__name__)

# 转码目标：MP3 320kbps 44.1kHz 立体声
TRANSCODE_ARGS = [
    "-vn", "-map", "0:a:0", "-map_metadata", "-1",
    "-af", "aresample=44100", "-c:a", "libmp3lame", "-b:a", "320k",
    "-ac", "2", "-f", "mp3",
]

# 无损/PCM/DSD 容器：一律转码（音箱基本都不稳）
HIRES_EXT = {".wav", ".aif", ".aiff", ".ape", ".wv", ".dsf", ".dff"}
# 其它容器（flac/m4a/…）按估算码率超过此阈值判定为高解析
BITRATE_THRESHOLD = 1_800_000  # bps（CD 级 flac≈0.9M、320k mp3≈0.32M 都远低于此）

_locks: dict[str, asyncio.Lock] = {}


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def should_transcode(path: Path, size: int, duration: float) -> bool:
    """廉价判定（不跑 ffprobe）：PCM/DSD 容器或估算码率过高。"""
    if path.suffix.lower() in HIRES_EXT:
        return True
    if duration and duration > 0 and size > 0:
        if size * 8 / duration > BITRATE_THRESHOLD:
            return True
    return False


def cache_path(data_dir: Path, track_id: int, src: Path) -> Path:
    st = src.stat()
    key = hashlib.md5(
        f"{track_id}:{int(st.st_mtime)}:{st.st_size}".encode()).hexdigest()
    return Path(data_dir) / "transcode_cache" / f"{key}.mp3"


async def ensure_cache(data_dir: Path, track_id: int, src: Path) -> Path | None:
    """转码为缓存 MP3（原子写 + 并发锁）。已存在直接返回；失败返回 None。"""
    out = cache_path(data_dir, track_id, src)
    if out.is_file() and out.stat().st_size > 0:
        return out
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    lock = _locks.setdefault(str(out), asyncio.Lock())
    async with lock:
        if out.is_file() and out.stat().st_size > 0:
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".part")
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            *TRANSCODE_ARGS, str(part),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        try:
            rc = await asyncio.wait_for(proc.wait(), timeout=600)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            part.unlink(missing_ok=True)
            return None
        if rc != 0 or not part.is_file() or part.stat().st_size == 0:
            part.unlink(missing_ok=True)
            return None
        part.replace(out)
        return out


class TranscodeWorker:
    """后台预转码：把高解析曲目提前转好，播放时命中静态缓存。"""

    def __init__(self, storage, settings):
        self.storage = storage
        self.settings = settings
        self._pending: set[int] = set()
        self._task: asyncio.Task | None = None

    def enabled(self) -> bool:
        return self.settings.data_dir is not None and ffmpeg_available()

    async def is_on(self) -> bool:
        return (await self.storage.get_setting("media_transcode_hires", "false")) == "true"

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="transcode-worker")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None

    def enqueue(self, track_id: int) -> None:
        self._pending.add(track_id)

    async def enqueue_hi_res(self) -> int:
        """扫描本地曲目，把需要转码且未缓存的加入队列。"""
        n = 0
        for t in await self.storage.all_local_tracks():
            src = Path(t["path"])
            if not src.is_file():
                continue
            if should_transcode(src, src.stat().st_size, float(t.get("duration") or 0)):
                cp = cache_path(self.settings.data_dir, t["id"], src)
                if not (cp.is_file() and cp.stat().st_size > 0):
                    self.enqueue(t["id"])
                    n += 1
        return n

    async def _loop(self) -> None:
        while True:
            try:
                if not await self.is_on():
                    await asyncio.sleep(15)
                    continue
                if not self._pending:
                    await asyncio.sleep(10)
                    continue
                tid = self._pending.pop()
                track = await self.storage.get_track(tid)
                if not track:
                    continue
                src = Path(track["path"])
                if not src.is_file():
                    continue
                await ensure_cache(self.settings.data_dir, tid, src)
                await asyncio.sleep(1)  # 低优先级，让出 CPU
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.debug("预转码单曲失败", exc_info=True)
                await asyncio.sleep(2)
