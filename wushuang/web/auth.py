"""管理鉴权：单管理员密码 + 服务端会话（SQLite）。

/media 媒体流不鉴权（音箱无法登录），其余页面与 API 均需会话。
服务只应暴露于局域网。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from ..config import Settings
from ..storage import Storage

COOKIE_NAME = "wushuang_session"
SESSION_DAYS = 7


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


async def init_admin_password(storage: Storage, settings: Settings) -> None:
    """初始化管理密码。

    admin_source 标记密码来源：default=内置默认（可被环境/向导密码覆盖）、
    env=部署配置注入、ui=用户在后台修改（永不被自动覆盖）。
    这解决了套件 worker 先于 postinst 启动、.env 尚未就位的时序问题。
    """
    if await storage.get_setting("admin_hash"):
        source = await storage.get_setting("admin_source", "")
        if source == "default" and settings.admin_password not in ("", "wushuang"):
            salt = secrets.token_hex(16)
            await storage.set_setting("admin_salt", salt)
            await storage.set_setting("admin_hash", _hash(settings.admin_password, salt))
            await storage.set_setting("admin_source", "env")
        return
    salt = secrets.token_hex(16)
    await storage.set_setting("admin_salt", salt)
    await storage.set_setting("admin_hash", _hash(settings.admin_password, salt))
    await storage.set_setting(
        "admin_source", "default" if settings.admin_password == "wushuang" else "env"
    )


async def verify_password(storage: Storage, password: str) -> bool:
    salt = await storage.get_setting("admin_salt", "")
    expected = await storage.get_setting("admin_hash", "")
    if not expected:
        return False
    return hmac.compare_digest(_hash(password, salt), expected)


async def change_password(storage: Storage, old: str, new: str) -> bool:
    if not await verify_password(storage, old):
        return False
    salt = secrets.token_hex(16)
    await storage.set_setting("admin_salt", salt)
    await storage.set_setting("admin_hash", _hash(new, salt))
    await storage.set_setting("admin_source", "ui")
    return True


async def create_session(storage: Storage) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    await storage.create_session(token, expires.isoformat(timespec="seconds"))
    return token


async def check_session(storage: Storage, token: str | None) -> bool:
    if not token:
        return False
    sess = await storage.get_session(token)
    if not sess:
        return False
    try:
        expires = datetime.fromisoformat(sess["expires_at"])
    except ValueError:
        return False
    return expires > datetime.now(timezone.utc)


async def is_admin(request: Request) -> bool:
    storage: Storage = request.app.state.storage
    return await check_session(storage, request.cookies.get(COOKIE_NAME))


async def require_admin_page(request: Request):
    if not await is_admin(request):
        raise HTTPException(status_code=307, headers={"Location": "/login"})
    return True


async def require_admin_api(request: Request) -> bool:
    if not await is_admin(request):
        raise HTTPException(status_code=401, detail="未登录")
    return True


def login_redirect() -> RedirectResponse:
    return RedirectResponse("/login", status_code=303)
