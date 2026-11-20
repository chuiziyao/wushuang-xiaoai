"""ncm-api（NeteaseCloudMusicApi 系）HTTP 客户端。

协议层完全交给上游 sidecar：扫码登录、Cookie 登录态、歌单详情、
歌曲播放地址（VIP 账号可取 VIP 音质）。本客户端只做 HTTP 调用与解析。
"""

from __future__ import annotations

import logging

import httpx

_LOGGER = logging.getLogger(__name__)


class NcmError(Exception):
    pass


class NcmClient:
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
        try:
            resp = await self._client.get(
                self.base_url + path, params=params, headers=headers
            )
        except httpx.HTTPError as e:
            raise NcmError(f"ncm-api 不可达（{self.base_url}）: {e}") from e
        if resp.status_code != 200:
            raise NcmError(f"ncm-api HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError as e:
            raise NcmError(f"ncm-api 响应非 JSON: {resp.text[:200]}") from e

    # ---------- 登录 ----------
    async def qr_key(self) -> str:
        obj = await self._get("/login/qr/key")
        return obj["data"]["unikey"]

    async def qr_create(self, unikey: str) -> dict:
        obj = await self._get("/login/qr/create", key=unikey, qrimg="true")
        return obj["data"]  # {qrurl, qrimg(base64)}

    async def qr_check(self, unikey: str) -> dict:
        """code: 800 过期 / 801 等待扫码 / 802 已扫码待确认 / 803 成功(带 cookie)。"""
        return await self._get("/login/qr/check", key=unikey)

    async def login_status(self) -> dict:
        obj = await self._get("/login/status")
        data = obj.get("data") or {}
        account = data.get("account") or {}
        profile = data.get("profile") or {}
        return {
            "logged_in": data.get("code") == 200 and bool(account.get("id")),
            "uid": str(account.get("id") or profile.get("userId") or ""),
            "nickname": profile.get("nickname") or "",
        }

    # ---------- 歌单与歌曲 ----------
    async def user_playlists(self, uid: str) -> list[dict]:
        obj = await self._get("/user/playlist", uid=uid, limit=1000)
        return obj.get("playlist") or []

    async def playlist_tracks(self, playlist_id: str) -> list[dict]:
        songs: list[dict] = []
        offset = 0
        while True:
            obj = await self._get(
                "/playlist/track/all", id=playlist_id, limit=500, offset=offset
            )
            batch = obj.get("songs") or []
            songs.extend(batch)
            if len(batch) < 500:
                break
            offset += 500
            if offset > 10000:  # 防御异常循环
                break
        return songs

    async def song_urls(self, ids: list[str], level: str = "exhigh") -> dict[str, dict]:
        """返回 {song_id: {url, type, br}}；url 为 None 表示无版权/需更高权益。

        优先 /song/url/v1（xeapi 通道，新音质分级）；该通道依赖启动时注册的
        xeapi 公钥，偶发不可用时自动回退 /song/url（eapi 通道，br 码率）。
        """
        level_br = {"standard": 128000, "higher": 192000, "exhigh": 320000,
                    "lossless": 999000, "hires": 999000}
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            joined = ",".join(batch)
            items = None
            try:
                obj = await self._get("/song/url/v1", id=joined, level=level)
                if obj.get("code") == 200 and obj.get("data"):
                    items = obj["data"]
            except NcmError:
                items = None
            if items is None:
                obj = await self._get(
                    "/song/url", id=joined, br=level_br.get(level, 320000)
                )
                items = obj.get("data") or []
            for item in items:
                out[str(item.get("id"))] = {
                    "url": item.get("url"),
                    "type": (item.get("type") or "mp3").lower(),
                    "br": item.get("br") or 0,
                }
        return out

    async def close(self) -> None:
        await self._client.aclose()
