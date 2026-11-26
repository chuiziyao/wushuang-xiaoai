"""媒体流服务：音箱通过 http://NAS:8080/media/<id> 拉取音乐。

自行实现 Range 请求（206），兼容各版本 starlette，保证音箱拖动/续播。
路径严格校验在音乐目录内，防止目录穿越。本路由不鉴权（音箱无法登录），
其余页面与 API 均需管理员会话。

高解析兼容：小爱音箱（LX06/S12A 等）只能稳播 44.1kHz/16bit 级别音频，
遇到 192kHz/24bit WAV/FLAC 等高解析文件会"起播后缓冲耗尽→无声，
且因机型 play_status 恒为播放中，要等满时长才切歌"。检测到这类文件时，
用 ffmpeg 实时转码为 44.1kHz/16bit MP3 推流（与主流音乐播放器一致）。
容器/系统无 ffmpeg 时自动回退为原样流式（不阻断播放）。
"""

from __future__ import annotations

import asyncio
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
    "-ac", "2", "-f", "mp3", "pipe:1",
]


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


def _needs_transcode(info: dict, path: Path, size: int, duration: float) -> bool:
    """判定音箱无法稳播的高解析/无损：PCM(wav/aiff)、DSD、采样率>48k、位深>16。"""
    if not info:
        # 无 ffprobe 信息时按扩展名兜底：PCM/DSD 一律转码
        return path.suffix.lower() in {".wav", ".aif", ".aiff", ".dsf", ".dff", ".ape", ".wv"}
    codec = (info.get("codec_name") or "").lower()
    if codec in {"pcm_s16le", "pcm_s24le", "pcm_s24be", "pcm_f32le", "pcm_f64le",
                 "pcm_s8", "pcm_u8", "dsd_lsb", "dsd_msb"}:
        return True
    try:
        sr = int(info.get("sample_rate") or 0)
    except (TypeError, ValueError):
        sr = 0
    if sr > 48000:
        return True
    bd = info.get("bits_per_raw_sample") or info.get("bits_per_sample")
    try:
        bd = int(bd or 0)
    except (TypeError, ValueError):
        bd = 0
    if bd > 16:
        return True
    return False


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
            # 在线音源：引擎通常直推原始 URL；此处兜底重定向
            return RedirectResponse(str(track["path"]), status_code=307)
        path = Path(track["path"]).resolve()
        music_root = settings.music_dir.resolve()
        if not str(path).startswith(str(music_root)) or not path.is_file():
            raise HTTPException(404, "file missing")

        size = path.stat().st_size

        # 高解析实时转码：默认关闭（保留原始音质），开启且系统有 ffmpeg 时才转码；
        # 无 ffmpeg 时自动跳过（不阻断播放）
        transcode = (await storage.get_setting("media_transcode_hires", "false")) == "true"
        if transcode and _ffmpeg_bin():
            info = await _probe_audio(path)
            if _needs_transcode(info, path, size, float(track.get("duration") or 0)):
                return await _transcode_response(path)

        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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

        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
            "Content-Type": ctype,
        }
        if partial:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return StreamingResponse(
            iter_file(), status_code=206 if partial else 200, headers=headers
        )

    async def _transcode_response(path: Path) -> StreamingResponse:
        """ffmpeg 解码→重采样→MP3 管道流式（忽略 Range，从头连续推流）。"""
        ffmpeg = _ffmpeg_bin() or "ffmpeg"
        proc = await asyncio.create_subprocess_exec(
            ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
            *_TRANSCODE_ARGS,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

        async def gen():
            try:
                while True:
                    chunk = await proc.stdout.read(_CHUNK)
                    if not chunk:
                        break
                    yield chunk
            finally:
                if proc.returncode is None:
                    try:
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except Exception:  # noqa: BLE001 - 客户端断开/超时尽力回收
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            pass

        return StreamingResponse(
            gen(), status_code=200,
            media_type="audio/mpeg",
            headers={"Accept-Ranges": "none"},
        )

    return router
