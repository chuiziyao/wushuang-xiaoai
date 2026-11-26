"""媒体流服务：音箱通过 http://NAS:8080/media/<id> 拉取音乐。

路径严格校验在音乐目录内，防止目录穿越。本路由不鉴权（音箱无法登录），
其余页面与 API 均需管理员会话。

Range/续播交给 Starlette FileResponse 原生处理（高效、支持拖动）。
高解析无损（PCM/DSD/采样率>48k/位深>16）在开启 media_transcode_hires 后，
用 ffmpeg 转成 44.1kHz/16bit MP3 缓存文件再静态推流；判定只用 size/时长
估算码率，**不在每次请求跑 ffprobe**（避免每请求延迟导致音箱卡顿）。
后台 TranscodeWorker 提前转好，播放基本直接命中缓存。无 ffmpeg 自动原样流。
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .library import is_online_track
from .transcode import ensure_cache, ffmpeg_available, should_transcode


def build_media_router() -> APIRouter:
    router = APIRouter()

    @router.get("/media/{track_id}")
    async def media(track_id: int, request: Request):
        st = request.app.state
        track = await st.storage.get_track(track_id)
        if not track:
            raise HTTPException(404, "track not found")
        if is_online_track(track):
            return RedirectResponse(str(track["path"]), status_code=307)
        path = Path(track["path"]).resolve()
        music_root = st.settings.music_dir.resolve()
        if not str(path).startswith(str(music_root)) or not path.is_file():
            raise HTTPException(404, "file missing")

        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

        # 高解析实时/缓存转码（默认关闭；无 ffmpeg 自动跳过）
        if ((await st.storage.get_setting("media_transcode_hires", "false")) == "true"
                and ffmpeg_available()):
            size = path.stat().st_size
            dur = float(track.get("duration") or 0)
            if should_transcode(path, size, dur):
                # 未缓存则入队后台预转码，本次阻塞转一次（之后命中缓存秒开）
                cached = await ensure_cache(st.settings.data_dir, track_id, path)
                if cached is not None:
                    return FileResponse(cached, media_type="audio/mpeg")
                # 转码失败：回退原样流

        return FileResponse(path, media_type=ctype)

    return router
