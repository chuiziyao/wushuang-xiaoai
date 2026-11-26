"""汽水音乐平台客户端：qishui-api sidecar（MIT）HTTP 调用 + 归一化。

响应统一 {code: 0, message, data}；登录态 Cookie 为 "sessionid=xxx"（抖音扫码）。
已知限制：VIP 加密音频（has_spade_a=true）暂不支持下载，song_urls 返回 None，
同步时计入失败并提示；免费/非加密曲目正常下载。
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from ..platforms import QR_DONE, QR_EXPIRED, QR_SCANNED, QR_WAITING

_LOGGER = logging.getLogger(__name__)


class QishuiError(Exception):
    pass


def _audio_ext(url: str) -> str:
    path = urlparse(url).path
    if "." in path.rsplit("/", 1)[-1]:
        return path.rsplit(".", 1)[-1].lower()
    return "mp3"


class QishuiClient:
    name = "qishui"

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

    async def _request(self, method: str, path: str, *,
                       params: dict | None = None, json: dict | None = None) -> dict:
        headers = {}
        if self._cookie:
            headers["Cookie"] = self._cookie
        try:
            resp = await self._client.request(
                method, self.base_url + path, params=params, json=json,
                headers=headers,
            )
        except httpx.HTTPError as e:
            raise QishuiError(f"qishui-api 不可达（{self.base_url}）: {e}") from e
        if resp.status_code != 200:
            raise QishuiError(f"qishui-api HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            obj = resp.json()
        except ValueError as e:
            raise QishuiError(f"qishui-api 响应非 JSON: {resp.text[:200]}") from e
        if obj.get("code") != 0:
            raise QishuiError(f"qishui-api 错误: {obj.get('message') or str(obj)[:200]}")
        data = obj.get("data")
        return {} if data is None else data

    async def _get(self, path: str, **params) -> dict:
        return await self._request("GET", path, params=params or None)

    async def _post(self, path: str, json: dict | None = None) -> dict:
        return await self._request("POST", path, json=json or {})

    # ---------- 登录 ----------
    async def login_status(self) -> dict:
        if not self._cookie:
            return {"logged_in": False, "uid": "", "nickname": ""}
        data = await self._post("/auth/me")
        profile = data.get("profile") or {}
        uid = str(profile.get("id") or "")
        nickname = profile.get("nickname") or ""
        return {"logged_in": bool(uid or nickname), "uid": uid, "nickname": nickname}

    async def qr_start(self, login_type: str = "") -> dict:
        data = await self._get("/auth/qrcode")
        img = data.get("qrcode") or ""
        if img and not img.startswith("data:"):
            img = "data:image/png;base64," + img
        return {"key": data.get("token", ""), "qrimg": img,
                "qrurl": data.get("qrcode_index_url", "")}

    async def qr_check(self, key: str) -> dict:
        data = await self._post("/auth/qrcode/status", {"token": key})
        status = str(data.get("status") or "")
        # 抖音 check_qrconnect：1 等待扫码 / 2 已扫待确认 / 3 确认 / 4 过期 / 5 拒绝
        state = {"1": QR_WAITING, "2": QR_SCANNED, "3": QR_DONE,
                 "4": QR_EXPIRED, "5": QR_EXPIRED}.get(status, QR_WAITING)
        sid = (data.get("auth") or {}).get("sessionid") or ""
        cookie = f"sessionid={sid}" if state == QR_DONE and sid else None
        return {"state": state, "cookie": cookie,
                "message": "扫码被拒绝" if status == "5" else ""}

    # ---------- 歌单与歌曲 ----------
    async def user_playlists(self, uid: str) -> list[dict]:
        data = await self._post("/me/playlists")
        out = []
        for p in data.get("playlists") or []:
            if not p.get("id"):
                continue
            out.append({"id": str(p["id"]), "name": p.get("title", ""),
                        "trackCount": p.get("count_tracks") or 0})
        return out

    async def playlist_tracks(self, remote_id: str) -> list[dict]:
        songs: list[dict] = []
        cursor = ""
        while True:
            params = {"playlist_id": remote_id, "count": 50}
            if cursor:
                params["cursor"] = cursor
            data = await self._get("/playlist/detail", **params)
            batch = data.get("media_resources") or []
            for item in batch:
                t = item.get("track") or {}
                if not t.get("id"):
                    continue  # 视频等非曲目资源
                songs.append({
                    "id": str(t["id"]),
                    "name": t.get("name") or "未知歌曲",
                    "artist": ", ".join(a.get("name", "") for a in t.get("artists") or []),
                    "album": (t.get("album") or {}).get("name", ""),
                })
            cursor = data.get("next_cursor") or ""
            if not cursor or not batch or len(songs) >= 10000:
                break
        return songs

    async def song_urls(self, ids: list[str], level: str = "default") -> dict[str, dict]:
        out: dict[str, dict] = {}
        sem = asyncio.Semaphore(3)

        async def _one(tid: str) -> None:
            async with sem:
                try:
                    data = await self._get("/download/url", track_id=tid)
                except QishuiError as e:
                    _LOGGER.warning("汽水取地址失败 %s: %s", tid, e)
                    out[tid] = {"url": None, "type": "mp3", "br": 0}
                    return
                if data.get("has_spade_a"):
                    _LOGGER.info("汽水加密音频（VIP）暂不支持: %s", tid)
                    out[tid] = {"url": None, "type": "mp3", "br": 0}
                    return
                url = data.get("audio_url") or ""
                out[tid] = {"url": url or None,
                            "type": _audio_ext(url) if url else "mp3", "br": 0}

        await asyncio.gather(*[_one(str(t)) for t in ids])
        return out

    async def close(self) -> None:
        await self._client.aclose()
