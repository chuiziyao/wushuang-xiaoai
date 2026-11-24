"""媒体流服务：音箱通过 http://NAS:8080/media/<id> 拉取音乐。

自行实现 Range 请求（206），兼容各版本 starlette，保证音箱拖动/续播。
路径严格校验在音乐目录内，防止目录穿越。本路由不鉴权（音箱无法登录），
其余页面与 API 均需管理员会话。
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_CHUNK = 256 * 1024


def build_media_router() -> APIRouter:
    router = APIRouter()

    @router.get("/media/{track_id}")
    async def media(track_id: int, request: Request):
        storage = request.app.state.storage
        settings = request.app.state.settings
        track = await storage.get_track(track_id)
        if not track:
            raise HTTPException(404, "track not found")
        path = Path(track["path"]).resolve()
        music_root = settings.music_dir.resolve()
        if not str(path).startswith(str(music_root)) or not path.is_file():
            raise HTTPException(404, "file missing")

        size = path.stat().st_size
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

    return router
