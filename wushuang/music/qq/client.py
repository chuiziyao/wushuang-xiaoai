"""QQ音乐平台客户端：QQMusicApi Web 服务（GPLv3 上游，独立 sidecar）HTTP 调用 + 归一化。

响应统一 {code: 0, msg, data}；登录态 Cookie 为 "musicid=xxx; musickey=yyy"。
播放地址为 CDN 域名 + purl 相对路径（先 /song/get_cdn_dispatch 取节点）。
音质参数是 sidecar SONG_FILE_TYPES 元组索引：MP3_128=13 / MP3_320=12 / FLAC=7。
"""

from __future__ import annotations

import logging

import httpx

from ..platforms import QR_DONE, QR_EXPIRED, QR_SCANNED, QR_WAITING

_LOGGER = logging.getLogger(__name__)

# sidecar web/src/modules/song.py SONG_FILE_TYPES 顺序索引
_QUALITY_INDEX = {"128": 13, "320": 12, "flac": 7}
_CREDENTIAL_FIELDS = ("musicid", "musickey", "openid", "refresh_token",
                      "access_token", "unionid", "str_musicid", "refresh_key",
                      "expired_at")


class QQError(Exception):
    pass


def _cookie_value(cookie: str, key: str) -> str:
    for part in (cookie or "").split(";"):
        part = part.strip()
        if part.startswith(f"{key}="):
            return part[len(key) + 1:]
    return ""


class QQClient:
    name = "qq"

    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._cookie = ""
        self._qr_type = "qq"
        self._cdn: str | None = None
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
            raise QQError(f"qq-api 不可达（{self.base_url}）: {e}") from e
        if resp.status_code != 200:
            raise QQError(f"qq-api HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            obj = resp.json()
        except ValueError as e:
            raise QQError(f"qq-api 响应非 JSON: {resp.text[:200]}") from e
        if obj.get("code") != 0:
            raise QQError(f"qq-api 错误: {obj.get('msg') or str(obj)[:200]}")
        data = obj.get("data")
        return {} if data is None else data

    async def _get(self, path: str, **params) -> dict:
        return await self._request("GET", path, params=params or None)

    # ---------- 登录 ----------
    async def login_status(self) -> dict:
        musicid = _cookie_value(self._cookie, "musicid")
        musickey = _cookie_value(self._cookie, "musickey")
        if not musicid or not musickey:
            return {"logged_in": False, "uid": "", "nickname": ""}
        expired = await self._get("/login/check_expired")
        # data 为 bool：True 表示已过期
        logged_in = expired is False or expired == 0
        nickname = f"QQ用户{musicid}"
        if logged_in:
            try:
                data = await self._get(f"/user/{musicid}/created_songlists")
                for p in data.get("playlists") or []:
                    if p.get("nick"):
                        nickname = p["nick"]
                        break
            except QQError:
                pass  # 昵称取不到不影响登录态判断
        return {"logged_in": bool(logged_in), "uid": musicid, "nickname": nickname}

    async def qr_start(self, login_type: str = "") -> dict:
        self._qr_type = login_type if login_type in ("qq", "wx") else "qq"
        data = await self._get(f"/login/qrcode/{self._qr_type}")
        return {"key": data.get("identifier", ""),
                "qrimg": data.get("img", ""), "qrurl": ""}

    async def qr_check(self, key: str) -> dict:
        data = await self._get(f"/login/qrcode/{self._qr_type}/status",
                               identifier=key)
        event = data.get("event")
        state = {0: QR_DONE, 1: QR_WAITING, 2: QR_SCANNED,
                 3: QR_EXPIRED, 4: QR_EXPIRED}.get(event, QR_WAITING)
        cookie = None
        cred = data.get("credential") or {}
        if event == 0 and cred.get("musickey"):
            cookie = "; ".join(
                f"{k}={cred[k]}" for k in _CREDENTIAL_FIELDS
                if cred.get(k) not in (None, "")
            )
        return {"state": state, "cookie": cookie,
                "message": "扫码被拒绝" if event == 4 else ""}

    # ---------- 歌单与歌曲 ----------
    async def user_playlists(self, uid: str) -> list[dict]:
        data = await self._get(f"/user/{uid}/created_songlists")
        out = []
        for p in data.get("playlists") or []:
            pid = p.get("id") or p.get("dirid")
            if not pid:
                continue
            out.append({"id": str(pid), "name": p.get("title", ""),
                        "trackCount": p.get("songnum") or 0})
        return out

    async def playlist_tracks(self, remote_id: str) -> list[dict]:
        songs: list[dict] = []
        page = 1
        while True:
            data = await self._get(f"/songlist/{remote_id}/detail",
                                   num=100, page=page)
            batch = data.get("songs") or []
            for s in batch:
                mid = s.get("mid") or ""
                if not mid:
                    continue
                singers = s.get("singer") or []
                songs.append({
                    "id": mid,
                    "name": s.get("name") or s.get("title") or "未知歌曲",
                    "artist": ", ".join(a.get("name", "") for a in singers),
                    "album": (s.get("album") or {}).get("name", "")
                    or (s.get("album") or {}).get("title", ""),
                })
            total = data.get("total") or 0
            if len(batch) < 100 or len(songs) >= min(total, 10000):
                break
            page += 1
        return songs

    async def _get_cdn(self) -> str:
        if self._cdn:
            return self._cdn
        data = await self._get("/song/get_cdn_dispatch")
        sip = data.get("sip") or []
        self._cdn = sip[0] if sip else "https://dl.stream.qqmusic.qq.com/"
        return self._cdn

    async def song_urls(self, ids: list[str], level: str = "320") -> dict[str, dict]:
        cdn = await self._get_cdn()
        file_type = _QUALITY_INDEX.get(level, _QUALITY_INDEX["320"])
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            batch = ids[i:i + 100]
            data = await self._request(
                "POST", "/song/get_song_urls",
                json={"file_info": [{"mid": m} for m in batch],
                      "file_type": file_type},
            )
            items = data.get("data") or data.get("midurlinfo") or []
            for item in items:
                mid = item.get("mid") or ""
                purl = item.get("purl") or ""
                ok = item.get("result", 0) == 0 and bool(purl)
                filename = item.get("filename") or ""
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "mp3"
                out[mid] = {"url": cdn + purl if ok else None,
                            "type": ext, "br": 0}
        return out

    async def close(self) -> None:
        await self._client.aclose()
