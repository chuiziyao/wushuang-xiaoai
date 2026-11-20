"""酷狗音乐平台客户端：KuGouMusicApi sidecar HTTP 调用 + 归一化。

sidecar 返回酷狗原始 JSON（{status, error_code, data}）；登录态为
"token=xxx; userid=xxx"，经 Cookie 头传递。sidecar 有 2 分钟 URL 缓存，
所有请求附带时间戳避免扫码轮询拿到旧状态。
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..platforms import QR_DONE, QR_EXPIRED, QR_SCANNED, QR_WAITING

_LOGGER = logging.getLogger(__name__)


class KugouError(Exception):
    pass


def _cookie_value(cookie: str, key: str) -> str:
    for part in (cookie or "").split(";"):
        part = part.strip()
        if part.startswith(f"{key}="):
            return part[len(key) + 1:]
    return ""


class KugouClient:
    name = "kugou"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._cookie = ""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0), transport=transport
        )

    def set_cookie(self, cookie: str) -> None:
        self._cookie = cookie or ""

    @property
    def has_cookie(self) -> bool:
        return bool(self._cookie)

    async def _get(self, path: str, **params) -> dict:
        headers = {}
        if self._cookie:
            headers["Cookie"] = self._cookie
        params.setdefault("timestamp", int(time.time() * 1000))
        try:
            resp = await self._client.get(
                self.base_url + path, params=params, headers=headers
            )
        except httpx.HTTPError as e:
            raise KugouError(f"kugou-api 不可达（{self.base_url}）: {e}") from e
        if resp.status_code != 200:
            raise KugouError(f"kugou-api HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise KugouError(f"kugou-api 响应非 JSON: {resp.text[:200]}") from e

    # ---------- 登录 ----------
    async def login_status(self) -> dict:
        obj = await self._get("/user/detail")
        data = obj.get("data") or {}
        uid = str(data.get("userid") or _cookie_value(self._cookie, "userid") or "")
        nickname = data.get("nickname") or data.get("username") or ""
        return {
            "logged_in": obj.get("status") == 1 and bool(uid) and bool(nickname),
            "uid": uid,
            "nickname": nickname,
        }

    async def qr_start(self, login_type: str = "") -> dict:
        obj = await self._get("/login/qr/key")
        key = (obj.get("data") or {}).get("qrcode") or ""
        if not key:
            raise KugouError(f"获取二维码 key 失败: {str(obj)[:200]}")
        obj = await self._get("/login/qr/create", key=key, qrimg="true")
        data = obj.get("data") or {}
        return {"key": key, "qrimg": data.get("base64", ""),
                "qrurl": data.get("url", "")}

    async def qr_check(self, key: str) -> dict:
        obj = await self._get("/login/qr/check", key=key)
        data = obj.get("data") or {}
        status = data.get("status")
        state = {0: QR_EXPIRED, 1: QR_WAITING,
                 2: QR_SCANNED, 4: QR_DONE}.get(status, QR_WAITING)
        cookie = None
        if status == 4 and data.get("token"):
            cookie = f"token={data['token']}; userid={data.get('userid', '')}"
        return {"state": state, "cookie": cookie,
                "message": obj.get("error") or data.get("error") or ""}

    # ---------- 歌单与歌曲 ----------
    async def user_playlists(self, uid: str) -> list[dict]:
        out: list[dict] = []
        page = 1
        while True:
            obj = await self._get("/user/playlist", page=page, pagesize=100)
            data = obj.get("data") or {}
            batch = data.get("list") or data.get("lists") or []
            for p in batch:
                pid = p.get("global_collection_id") or str(p.get("listid") or "")
                if not pid:
                    continue
                out.append({"id": str(pid), "name": p.get("name", ""),
                            "trackCount": p.get("count") or p.get("song_count") or 0})
            if len(batch) < 100 or len(out) >= 10000:
                break
            page += 1
        return out

    async def playlist_tracks(self, remote_id: str) -> list[dict]:
        songs: list[dict] = []
        page = 1
        while True:
            obj = await self._get("/playlist/track/all", id=remote_id,
                                  page=page, pagesize=100)
            data = obj.get("data") or {}
            batch = data.get("songs") or data.get("list") or []
            for s in batch:
                h = (s.get("hash") or "").lower()
                if not h:
                    continue
                singers = s.get("singerinfo") or []
                songs.append({
                    "id": h,
                    "name": s.get("name") or "未知歌曲",
                    "artist": ", ".join(a.get("name", "") for a in singers),
                    "album": (s.get("albuminfo") or {}).get("name", ""),
                })
            if len(batch) < 100 or len(songs) >= 10000:
                break
            page += 1
        return songs

    async def song_urls(self, ids: list[str], level: str = "320") -> dict[str, dict]:
        """按 hash 逐个取播放地址（sidecar 单 hash 接口），限并发。"""
        out: dict[str, dict] = {}
        sem = asyncio.Semaphore(5)

        async def _one(h: str) -> None:
            async with sem:
                try:
                    obj = await self._get("/song/url", hash=h, quality=level)
                except KugouError as e:
                    _LOGGER.warning("酷狗取地址失败 %s: %s", h, e)
                    out[h] = {"url": None, "type": "mp3", "br": 0}
                    return
                data = obj.get("data") or {}
                url = data.get("url")
                if isinstance(url, list):
                    url = url[0] if url else None
                out[h] = {
                    "url": url or None,
                    "type": (data.get("extName") or "mp3").lower(),
                    "br": data.get("bitrate") or 0,
                }

        await asyncio.gather(*[_one(h.lower()) for h in ids])
        return out

    async def close(self) -> None:
        await self._client.aclose()
