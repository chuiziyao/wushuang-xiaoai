"""小米账号管理：登录态持久化（加密存 SQLite）、自动重登、服务句柄。

MiService 的 MiTokenStore 是同步接口，这里用"内存缓存 + 异步落盘"桥接：
load_token 返回内存缓存（启动时从数据库预载），save_token 更新缓存并
调度异步持久化，避免在协议库内部引入事件循环依赖。
"""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

from ..crypto import SecretBox
from ..storage import Storage
from ..vendor.miservice import MiAccount, MiIOService, MiNAService, MiVerificationRequired
from .qrlogin import XiaomiQrLogin

_LOGGER = logging.getLogger(__name__)


class _DBTokenStore:
    def __init__(self, manager: "XiaomiAccountManager"):
        self._mgr = manager

    def load_token(self):
        return self._mgr._token_cache

    def save_token(self, token=None):
        self._mgr._token_cache = token
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._mgr._persist_token(token))


class XiaomiAccountManager:
    def __init__(self, storage: Storage, box: SecretBox):
        self.storage = storage
        self.box = box
        self._session: aiohttp.ClientSession | None = None
        self._token_cache: dict | None = None
        self.account: MiAccount | None = None
        self.mina: MiNAService | None = None
        self.miio: MiIOService | None = None
        self._qr: XiaomiQrLogin | None = None

    @property
    def ready(self) -> bool:
        return self.mina is not None

    async def load(self) -> None:
        """启动时从数据库恢复账号与 token。"""
        acc = await self.storage.get_xiaomi_account()
        if not acc:
            return
        token_json = self.box.decrypt(acc.get("token_enc"))
        self._token_cache = json.loads(token_json) if token_json else None
        self._build(acc["username"], self.box.decrypt(acc["password_enc"]))

    def _build(self, username: str, password: str) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=40)
            )
        self.account = MiAccount(
            self._session, username, password, token_store=_DBTokenStore(self)
        )
        self.mina = MiNAService(self.account)
        self.miio = MiIOService(self.account)

    async def configure(self, username: str, password: str) -> bool:
        """Web 后台保存账号并尝试登录。"""
        await self.storage.save_xiaomi_account(
            username, self.box.encrypt(password), token_enc=None
        )
        self._token_cache = None
        self._build(username, password)
        return await self.ensure_login()

    async def ensure_login(self) -> bool:
        if self.account is None:
            return False
        try:
            ok = await self.account.login("micoapi")
        except MiVerificationRequired as e:
            # 密码正确但小米要求安全验证：保留 deviceId，展示验证链接
            await self._persist_token(self._token_cache)
            _LOGGER.warning("小米登录需安全验证（新设备/异地）: %s", e.url[:100])
            return False
        await self.storage.update_xiaomi_token(
            self.box.encrypt(json.dumps(self._token_cache)) if ok and self._token_cache else None,
            "ok" if ok else "login_failed",
        )
        if ok:
            # 顺带登录 xiaomiio（MIoT 通道，备用）
            try:
                await self.account.login("xiaomiio")
            except MiVerificationRequired:
                pass  # 主通道已验证通过即可
            except Exception:  # noqa: BLE001 - MIoT 非必需
                _LOGGER.warning("xiaomiio 登录失败（不影响 mina 主通道）")
        return ok

    async def _persist_token(self, token: dict | None) -> None:
        try:
            if token and token.get("userId"):
                status = "ok"
            elif token and token.get("verificationUrl"):
                status = "verification_required"
            else:
                status = "logged_out"
            await self.storage.update_xiaomi_token(
                self.box.encrypt(json.dumps(token)) if token else None, status
            )
        except Exception:  # noqa: BLE001
            _LOGGER.exception("持久化小米 token 失败")

    async def status(self) -> dict:
        acc = await self.storage.get_xiaomi_account()
        if not acc:
            return {"configured": False, "status": "not_configured"}
        out = {
            "configured": True,
            "username": acc["username"],
            "status": acc["status"],
            "has_token": bool(self._token_cache),
        }
        if acc["status"] == "verification_required" and self._token_cache:
            out["verification_url"] = self._token_cache.get("verificationUrl", "")
        return out

    async def start_qr_login(self) -> dict:
        """米家/小米账号 APP 扫码登录：返回二维码图片 URL。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=40)
            )
        self._qr = XiaomiQrLogin(self._session)
        out = await self._qr.start()
        _LOGGER.info("小米扫码会话已建立 lp=%s", bool(self._qr.lp))
        return out

    async def poll_qr_login(self) -> dict:
        """轮询扫码结果；确认后换取 token 并建立服务句柄。"""
        if self._qr is None:
            _LOGGER.warning("扫码轮询时无活跃会话（idle）")
            return {"state": "idle"}
        payload = await self._qr.poll_once(timeout=30)
        if payload is None:
            return {"state": "waiting"}
        _LOGGER.info("扫码确认成功，换取 serviceToken userId=%s", payload.get("userId"))
        token = await self._qr.finish(payload)
        self._token_cache = token
        acc = await self.storage.get_xiaomi_account()
        username = acc["username"] if acc else str(token["userId"])
        self._build(username, "")
        await self._persist_token(token)
        # 回读校验，确保扫码成果落盘
        check = await self.storage.get_xiaomi_account()
        saved = json.loads(self.box.decrypt(check.get("token_enc") or "")) if check.get("token_enc") else {}
        if "passToken" not in saved:
            raise RuntimeError("扫码 token 持久化校验失败")
        self._qr = None
        return {"state": "done"}

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
