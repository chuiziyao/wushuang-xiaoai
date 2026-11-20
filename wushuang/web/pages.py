"""页面路由：服务端渲染（Jinja2），交互走 /api JSON 接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import APP_NAME, __version__
from ..llm.providers import PRESETS
from .auth import (
    COOKIE_NAME,
    create_session,
    is_admin,
    verify_password,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render(request: Request, name: str, active: str, status_code: int = 200, **extra):
    ctx = {
        "request": request,
        "app_name": APP_NAME,
        "version": __version__,
        "active": active,
        **extra,
    }
    # Starlette 1.x 签名：TemplateResponse(request, name, context)
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def build_pages_router() -> APIRouter:
    router = APIRouter()

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if await is_admin(request):
            return RedirectResponse("/", status_code=303)
        return _render(request, "login.html", "login", error="")

    @router.post("/login")
    async def login_submit(request: Request, password: str = Form(...)):
        storage = request.app.state.storage
        if not await verify_password(storage, password):
            return _render(request, "login.html", "login",
                           status_code=401, error="密码错误")
        token = await create_session(storage)
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(
            COOKIE_NAME, token, httponly=True, samesite="lax", max_age=7 * 86400
        )
        return resp

    @router.get("/logout")
    async def logout(request: Request):
        token = request.cookies.get(COOKIE_NAME)
        if token:
            await request.app.state.storage.delete_session(token)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE_NAME)
        return resp

    async def _guard(request: Request):
        if not await is_admin(request):
            return RedirectResponse("/login", status_code=303)
        return None

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        if (r := await _guard(request)) is not None:
            return r
        storage = request.app.state.storage
        manager = request.app.state.manager
        music = request.app.state.music
        devices = await storage.list_devices()
        for d in devices:
            d["now_playing"] = music.now_playing(d["did"])
            d["running"] = d["did"] in request.app.state.dispatcher.running_devices()
        return _render(
            request, "dashboard.html", "dashboard",
            devices=devices,
            xiaomi=await manager.status(),
            netease=await storage.get_netease_account() or {},
            providers=await storage.list_providers(),
            tracks=await storage.count_tracks(),
            chats=await storage.recent_chat_logs(limit=8),
            syncs=await storage.recent_sync_logs(limit=5),
            speaker_base_url=request.app.state.settings.speaker_base_url(),
        )

    @router.get("/accounts", response_class=HTMLResponse)
    async def accounts(request: Request):
        if (r := await _guard(request)) is not None:
            return r
        storage = request.app.state.storage
        providers = [
            {**p, "has_key": bool(p["api_key_enc"]), "api_key_enc": ""}
            for p in await storage.list_providers()
        ]
        return _render(
            request, "accounts.html", "accounts",
            xiaomi=await request.app.state.manager.status(),
            netease=await storage.get_netease_account() or {},
            providers=providers,
            presets=PRESETS,
        )

    @router.get("/devices", response_class=HTMLResponse)
    async def devices_page(request: Request):
        if (r := await _guard(request)) is not None:
            return r
        storage = request.app.state.storage
        devices = await storage.list_devices()
        running = request.app.state.dispatcher.running_devices()
        for d in devices:
            d["running"] = d["did"] in running
        return _render(request, "devices.html", "devices", devices=devices)

    @router.get("/music", response_class=HTMLResponse)
    async def music_page(request: Request):
        if (r := await _guard(request)) is not None:
            return r
        storage = request.app.state.storage
        return _render(
            request, "music.html", "music",
            playlists=await storage.list_playlists(),
            tracks=await storage.count_tracks(),
            netease=await storage.get_netease_account() or {},
            syncs=await storage.recent_sync_logs(limit=10),
            music_dir=str(request.app.state.settings.music_dir),
        )

    @router.get("/chat", response_class=HTMLResponse)
    async def chat_page(request: Request):
        if (r := await _guard(request)) is not None:
            return r
        storage = request.app.state.storage
        return _render(
            request, "chat.html", "chat",
            chats=await storage.recent_chat_logs(limit=100),
            devices=await storage.list_devices(),
        )

    @router.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        if (r := await _guard(request)) is not None:
            return r
        storage = request.app.state.storage
        settings = await storage.all_settings()
        settings.pop("admin_hash", None)
        settings.pop("admin_salt", None)
        return _render(
            request, "settings.html", "settings",
            runtime=settings,
            public_url=request.app.state.settings.public_url,
            speaker_base_url=request.app.state.settings.speaker_base_url(),
            ncm_api_url=request.app.state.settings.ncm_api_url,
        )

    return router
