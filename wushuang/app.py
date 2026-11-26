"""应用工厂与生命周期：组装存储、协议组件、各引擎与 Web 路由。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, __version__
from .config import DEFAULT_RUNTIME_SETTINGS, Settings
from .crypto import SecretBox
from .dispatcher import Dispatcher
from .llm.engine import LLMEngine
from .music.engine import MusicEngine
from .music.kugou.client import KugouClient
from .music.netease.client import NcmClient
from .music.netease.platform import NeteasePlatform
from .music.qq.client import QQClient
from .music.qishui.client import QishuiClient
from .music.scheduler import SyncScheduler
from .music.server import build_media_router
from .music.sync import PlaylistSync
from .music.transcode import TranscodeWorker
from .storage import Storage
from .web.auth import init_admin_password
from .xiaomi.account import XiaomiAccountManager
from .xiaomi.devices import refresh_devices

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_LOGGER = logging.getLogger("wushuang")

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os

    os.umask(0o022)  # 确保数据库/密钥文件可写（部分运行时默认 umask 异常）
    settings = Settings.from_env()
    storage = Storage(settings.db_path)
    await storage.open()
    box = SecretBox(settings.key_path)

    for key, value in DEFAULT_RUNTIME_SETTINGS.items():
        if await storage.get_setting(key) is None:
            await storage.set_setting(key, value)
    await init_admin_password(storage, settings)

    manager = XiaomiAccountManager(storage, box)
    await manager.load()
    ncm = NcmClient(settings.ncm_api_url)
    # 多平台客户端注册表：新平台在此接入（统一接口见 music/platforms.py）
    # 桌面版不做音乐平台登录/同步：不接入任何 sidecar，曲库=本地文件夹+在线音源
    clients: dict = {} if settings.desktop else {
        "netease": NeteasePlatform(ncm),
        "kugou": KugouClient(settings.kugou_api_url),
        "qq": QQClient(settings.qq_api_url),
        "qishui": QishuiClient(settings.qishui_api_url),
    }
    sync = PlaylistSync(storage, box, settings, clients)
    scheduler = SyncScheduler(storage, sync)
    music = MusicEngine(storage, settings)
    transcode = TranscodeWorker(storage, settings)
    llm = LLMEngine(storage, box)
    dispatcher = Dispatcher(manager, storage, music, llm)

    if manager.ready:
        try:
            tok = manager._token_cache or {}
            if "micoapi" in tok:
                # 已有可用 serviceToken：直接使用，启动不做重新登录
                # （避免密码验证流程覆盖扫码 token；API 401 时按需刷新）
                await refresh_devices(manager, storage)
                await dispatcher.sync()
            elif await manager.ensure_login():
                await refresh_devices(manager, storage)
                await dispatcher.sync()
        except Exception:  # noqa: BLE001 - 启动时登录失败不阻塞 Web 后台
            _LOGGER.warning("启动时小米登录失败，可在后台重新登录", exc_info=True)
    if not settings.desktop:
        await scheduler.start()
    # 后台预转码：开关开启且有 ffmpeg 时，提前把高解析转成缓存，播放秒开
    transcode.start()
    try:
        await transcode.enqueue_hi_res()
    except Exception:  # noqa: BLE001 - 预转码排队失败不影响启动
        _LOGGER.debug("启动预转码排队失败", exc_info=True)

    app.state.settings = settings
    app.state.storage = storage
    app.state.box = box
    app.state.manager = manager
    app.state.ncm = ncm
    app.state.clients = clients
    app.state.sync = sync
    app.state.scheduler = scheduler
    app.state.music = music
    app.state.transcode = transcode
    app.state.llm = llm
    app.state.dispatcher = dispatcher
    _LOGGER.info(
        "%s v%s 已启动：后台 http://%s:%s，音箱媒体地址 %s",
        APP_NAME, __version__, settings.host, settings.port,
        settings.speaker_base_url(),
    )
    try:
        yield
    finally:
        await dispatcher.stop_all()
        await scheduler.stop()
        await transcode.stop()
        await sync.cancel_all()
        for client in clients.values():
            await client.close()
        await manager.close()
        await storage.close()


def create_app() -> FastAPI:
    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan)

    from .web.api import build_api_router
    from .web.pages import build_pages_router

    app.include_router(build_media_router())  # 音箱拉流，不鉴权
    app.include_router(build_pages_router())
    app.include_router(build_api_router())
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")
    return app


app = create_app()
