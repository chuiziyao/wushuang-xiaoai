"""媒体流服务：音箱通过 http://NAS:8080/media/<id> 拉取音乐。

自行实现 Range 请求（206），兼容各版本 starlette，保证音箱拖动/续播。
路径严格校验在音乐目录内，防止目录穿越。本路由不鉴权（音箱无法登录），
其余页面与 API 均需管理员会话。

高解析兼容：小爱音箱（LX06/S12A 等）无法稳播高解析无损（192kHz/24bit
WAV/FLAC，码率 5–9Mbps），表现为"起播后没声/断续、要等很久才切歌"。
开启 media_transcode_hires（默认关闭）后，用 ffmpeg 把这类文件转码为
44.1kHz/16bit MP3 **缓存成文件**，再以带 Content-Length + Range 的普通方式
推流（避免 chunked 无长度流导致音箱缓冲卡顿；重播秒开、可拖动）。
系统无 ffmpeg 时自动回退原样流式（不阻断播放）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse

from .library import is_online_track

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 256 * 1024

# 转码目标：MP3 320kbps 44.1kHz 立体声（各机型通用、音质足够）
_TRANSCODE_ARGS = [
    "-vn", "-map", "0:a:0", "-map_metadata", "-1",
    "-af", "aresample=44100", "-c:a", "libmp3lame", "-b:a", "320k",
    "-ac", "2", "-f", "mp3",
]

_HIRES_EXT = {".wav", ".aif", ".aiff", ".dsf", ".dff", ".ape", ".wv"}
_pcm_locks: dict[str, asyncio.Lock] = {}


def _ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def _ffprobe_bin() -> str | None:
    return shutil.which("ffprobe")


async def _probe_audio(path: Path) -> dict:
    """ffprobe 读音频流头信息（毫秒级）；失败返回 {}。"""
    ffprobe = _ffprobe_bin()
    if not ffprobe:
        return {}
    cmd = [ffprobe, "-v", "error", "-select_streams", "a:0",
           "-show_entries",
           "stream=codec_name,sample_rate,bits_per_sample,bits_per_raw_sample,bit_rate",
           "-of", "json", str(path)]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        data = json.loads(out.decode() or "{}")
        streams = data.get("streams") or []
        return streams[0] if streams else {}
    except Exception:  # noqa: BLE001 - 探测失败不影响原样流式
        return {}


def _needs_transcode(info: dict, path: Path) -> bool:
    """判定音箱无法稳播的高解析/无损：PCM(wav/aiff)、DSD、采样率>48k、位深>16。"""
    if not info:
        return path.suffix.lower() in _HIRES_EXT
    codec = (info.get("codec_name") or "").lower()
    if codec.startswith("pcm") or codec.startswith("dsd"):
        return True
    try:
        if int(info.get("sample_rate") or 0) > 48000:
            return True
    except (TypeError, ValueError):
        pass
    bd = info.get("bits_per_raw_sample") or info.get("bits_per_sample")
    try:
        if int(bd or 0) > 16:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _serve_file(path: Path, request: Request, ctype: str) -> StreamingResponse:
    """带 Range/Content-Length 的普通文件流式（复用给原样流与转码缓存）。"""
    size = path.stat().st_size
    range_header = request.headers.get("range")
    start, end = 0, size - 1
    partial = False
    if range_header:
        m = _RANGE_RE.match(range_header.strip())
        if m:
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            elif m.group(2):  # suffix range: bytes=-N
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end or start >= size:
                raise HTTPException(416, "range not satisfiable")
            partial = True

    length = end - start + 1

    def iter_file():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(_CHUNK, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length),
               "Content-Type": ctype}
    if partial:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(iter_file(), status_code=206 if partial else 200,
                             headers=headers)


def build_media_router() -> APIRouter:
    router = APIRouter()

    @router.get("/media/{track_id}")
    async def media(track_id: int, request: Request):
        storage = request.app.state.storage
        settings = request.app.state.settings
        track = await storage.get_track(track_id)
        if not track:
            raise HTTPException(404, "track not found")
        if is_online_track(track):
            return RedirectResponse(str(track["path"]), status_code=307)
        path = Path(track["path"]).resolve()
        music_root = settings.music_dir.resolve()
        if not str(path).startswith(str(music_root)) or not path.is_file():
            raise HTTPException(404, "file missing")

        # 高解析实时转码：默认关闭；开启且系统有 ffmpeg 才转码，无 ffmpeg 自动跳过
        if ((await storage.get_setting("media_transcode_hires", "false")) == "true"
                and _ffmpeg_bin()):
            info = await _probe_audio(path)
            if _needs_transcode(info, path):
                cached = await _ensure_transcode_cache(settings, track_id, path)
                if cached is not None:
                    return _serve_file(cached, request, "audio/mpeg")
                # 转码失败：回退原样流（不阻断）

        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return _serve_file(path, request, ctype)

    async def _ensure_transcode_cache(settings, track_id: int,
                                      src: Path) -> Path | None:
        """转码为 MP3 缓存文件（按 track_id+源mtime+size 命名，原子写、并发锁）。"""
        st = src.stat()
        key = hashlib.md5(f"{track_id}:{int(st.st_mtime)}:{st.st_size}".encode()).hexdigest()
        cache_dir = Path(settings.data_dir) / "transcode_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        out = cache_dir / f"{key}.mp3"
        if out.is_file() and out.stat().st_size > 0:
            return out
        lock = _pcm_locks.setdefault(str(out), asyncio.Lock())
        async with lock:
            if out.is_file() and out.stat().st_size > 0:
                return out
            part = out.with_suffix(".part")
            ffmpeg = _ffmpeg_bin() or "ffmpeg"
            proc = await asyncio.create_subprocess_exec(
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                *_TRANSCODE_ARGS, str(part),
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

    return router
