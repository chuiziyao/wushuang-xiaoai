"""网易云平台适配：把 NcmClient 的原始响应归一化为统一平台接口。"""

from __future__ import annotations

from ..platforms import QR_DONE, QR_EXPIRED, QR_SCANNED, QR_WAITING
from .client import NcmClient


class NeteasePlatform:
    name = "netease"

    def __init__(self, ncm: NcmClient):
        self.ncm = ncm

    def set_cookie(self, cookie: str) -> None:
        self.ncm.set_cookie(cookie)

    @property
    def has_cookie(self) -> bool:
        return self.ncm.has_cookie

    async def login_status(self) -> dict:
        return await self.ncm.login_status()

    async def qr_start(self, login_type: str = "") -> dict:
        key = await self.ncm.qr_key()
        data = await self.ncm.qr_create(key)
        return {"key": key, "qrimg": data.get("qrimg", ""),
                "qrurl": data.get("qrurl", "")}

    async def qr_check(self, key: str) -> dict:
        r = await self.ncm.qr_check(key)
        code = r.get("code")
        state = {800: QR_EXPIRED, 801: QR_WAITING,
                 802: QR_SCANNED, 803: QR_DONE}.get(code, QR_WAITING)
        return {"state": state,
                "cookie": r.get("cookie") if code == 803 else None,
                "message": r.get("message", "")}

    async def user_playlists(self, uid: str) -> list[dict]:
        return [
            {"id": str(p["id"]), "name": p.get("name", ""),
             "trackCount": p.get("trackCount", 0)}
            for p in await self.ncm.user_playlists(uid)
        ]

    async def playlist_tracks(self, remote_id: str) -> list[dict]:
        songs = await self.ncm.playlist_tracks(remote_id)
        return [
            {"id": str(s.get("id") or ""),
             "name": s.get("name") or "未知歌曲",
             "artist": ", ".join(a.get("name", "") for a in s.get("ar") or []),
             "album": (s.get("al") or {}).get("name", "")}
            for s in songs
        ]

    async def song_urls(self, ids: list[str], level: str = "exhigh") -> dict[str, dict]:
        return await self.ncm.song_urls(ids, level)

    async def close(self) -> None:
        await self.ncm.close()
