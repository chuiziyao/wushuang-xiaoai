"""JSON API：账号、设备、歌单、服务商、设置等管理动作。"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..config import DEFAULT_RUNTIME_SETTINGS
from ..llm.client import stream_chat
from ..llm.providers import PRESETS
from ..music.engine import track_play_url
from ..music.library import playlist_folder, resolve_local_folder, scan_library
from ..music.platforms import PLATFORMS, QR_DONE, platform_overviews
from ..utils.text import sanitize_filename
from ..xiaomi.devices import refresh_devices
from .auth import change_password, require_admin_api

_LOGGER = logging.getLogger(__name__)


class XiaomiCreds(BaseModel):
    username: str
    password: str


class DeviceUpdate(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


class CookieIn(BaseModel):
    cookie: str


class PlaylistIn(BaseModel):
    name: str
    source: str = "local"
    remote_id: str = ""
    netease_id: str = ""  # 旧前端兼容：等价于 source=netease + remote_id
    cron: str = ""
    remark: str = ""
    folder: str = ""  # 本地歌单可指向已有歌曲目录（NAS 或容器路径）


class PlaylistMetaIn(BaseModel):
    name: str | None = None
    remark: str | None = None


class CronIn(BaseModel):
    cron: str


class ProviderIn(BaseModel):
    preset: str = "custom"
    name: str
    base_url: str
    api_key: str = ""
    model: str
    priority: int = 100
    enabled: bool = True
    uskey: str | None = None  # 元宝 x-uskey
    agent_id: str | None = None  # 元宝 agentId（可选）


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    priority: int | None = None
    enabled: bool | None = None
    uskey: str | None = None
    agent_id: str | None = None


class SettingIn(BaseModel):
    key: str
    value: str


class PasswordIn(BaseModel):
    old: str
    new: str


class ChatClear(BaseModel):
    did: str | None = None


class TtsTest(BaseModel):
    did: str
    text: str = "无双小爱同学已就绪"


class PlayTest(BaseModel):
    did: str
    track_id: int


class PlayerPlayIn(BaseModel):
    did: str
    track_id: int | None = None
    playlist_id: int | None = None


class PlayerControlIn(BaseModel):
    did: str
    action: str
    value: int | None = None


class PlayerNativeIn(BaseModel):
    did: str
    text: str


class PlayerModeIn(BaseModel):
    did: str
    mode: str  # wushuang | native


class PlayAllIn(BaseModel):
    did: str
    mode: str = "order"  # order | shuffle


class PlayGroupIn(BaseModel):
    did: str
    kind: str  # artist | album | folder
    name: str
    mode: str = "order"  # order | shuffle


class MakePlaylistIn(BaseModel):
    kind: str  # artist | album | folder
    name: str
    remark: str = ""


class PlaylistTracksIn(BaseModel):
    track_ids: list[int]


class OnlineIn(BaseModel):
    title: str
    url: str
    duration: float = 0.0  # 可选：已知时长（秒）；0=直播/未知，不超时切歌


class DesktopConfigIn(BaseModel):
    music_dir: str | None = None
    port: int | None = None
    public_url: str | None = None


_PLAYER_ACTIONS = {"pause", "resume", "stop", "next", "prev", "volume_up",
                   "volume_down", "volume_set", "loop_one", "loop_all", "shuffle"}


def _mask_provider(p: dict) -> dict:
    out = dict(p)
    out["has_key"] = bool(out.pop("api_key_enc", ""))
    return out


def _extract_web_key(preset: str, raw: str) -> str:
    """网页版预设：用户粘贴整段 Cookie 时自动提取所需令牌。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    wanted = {
        "doubao-web": ["sessionid_ss", "sessionid"],
        "qwen-web": ["tongyi_sso_ticket", "login_aliyunid_ticket"],
    }.get(preset)
    if wanted:
        for name in wanted:
            if f"{name}=" in raw:
                for part in raw.split(";"):
                    part = part.strip()
                    if part.startswith(f"{name}="):
                        return part[len(name) + 1:]
    return raw  # 元宝保留整段 Cookie；其他原样


def _merge_extra(existing: str | None, uskey: str | None,
                 agent_id: str | None) -> str:
    try:
        extra = json.loads(existing or "{}")
    except ValueError:
        extra = {}
    if uskey is not None:
        extra["uskey"] = uskey.strip()
    if agent_id is not None and agent_id.strip():
        extra["agent_id"] = agent_id.strip()
    return json.dumps(extra, ensure_ascii=False)


_COOKIE_ATTRS = {"max-age", "expires", "path", "domain", "secure", "httponly",
                 "samesite", "priority", "partitioned"}


def _clean_cookie(raw: str) -> str:
    """剔除 Set-Cookie 属性字段（Max-Age/Expires/Path 等），只保留名值对。"""
    parts = []
    for seg in (raw or "").split(";"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        key = seg.split("=", 1)[0].strip()
        if key.lower() in _COOKIE_ATTRS:
            continue
        parts.append(seg)
    return "; ".join(parts)


def build_api_router() -> APIRouter:
    router = APIRouter(prefix="/api", dependencies=[Depends(require_admin_api)])

    # ---------- 总览 ----------
    @router.get("/status")
    async def status(request: Request):
        st = request.app.state
        devices = await st.storage.list_devices()
        running = st.dispatcher.running_devices()
        for d in devices:
            d["running"] = d["did"] in running
            d["now_playing"] = st.music.now_playing(d["did"])
        return {
            "xiaomi": await st.manager.status(),
            "netease": await st.storage.get_music_account("netease") or {},
            "music": await st.storage.list_music_accounts(),
            "devices": devices,
            "tracks": await st.storage.count_tracks(),
            "providers": len(await st.storage.list_providers(enabled_only=True)),
            "chats": await st.storage.recent_chat_logs(limit=5),
            "syncs": await st.storage.recent_sync_logs(limit=5),
        }

    # ---------- 小米账号 ----------
    @router.post("/xiaomi")
    async def xiaomi_save(request: Request, body: XiaomiCreds):
        st = request.app.state
        ok = await st.manager.configure(body.username.strip(), body.password)
        if ok:
            await refresh_devices(st.manager, st.storage)
            await st.dispatcher.sync()
        return {"ok": ok, "status": await st.manager.status(),
                "devices": await st.storage.list_devices()}

    @router.post("/xiaomi/relogin")
    async def xiaomi_relogin(request: Request):
        st = request.app.state
        ok = await st.manager.ensure_login()
        if ok:
            await refresh_devices(st.manager, st.storage)
            await st.dispatcher.sync()
        return {"ok": ok, "status": await st.manager.status()}

    @router.post("/xiaomi/qr/start")
    async def xiaomi_qr_start(request: Request):
        try:
            return await request.app.state.manager.start_qr_login()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"获取二维码失败: {e}") from e

    @router.get("/xiaomi/qr/poll")
    async def xiaomi_qr_poll(request: Request):
        st = request.app.state
        try:
            r = await st.manager.poll_qr_login()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"扫码登录失败: {e}") from e
        if r.get("state") == "done":
            await refresh_devices(st.manager, st.storage)
            await st.dispatcher.sync()
            r["devices"] = await st.storage.list_devices()
        return r

    @router.post("/devices/refresh")
    async def devices_refresh(request: Request):
        st = request.app.state
        devices = await refresh_devices(st.manager, st.storage)
        await st.dispatcher.sync()
        return {"devices": devices}

    @router.post("/devices/{did}")
    async def device_update(request: Request, did: str, body: DeviceUpdate):
        st = request.app.state
        dev = await st.storage.get_device(did)
        if not dev:
            raise HTTPException(404, "设备不存在")
        if body.enabled is not None:
            await st.storage.set_device_enabled(did, body.enabled)
        if body.config is not None:
            await st.storage.set_device_config(did, body.config)
        await st.dispatcher.sync()
        return {"ok": True}

    # ---------- 音乐平台（网易云/酷狗/QQ/汽水，统一路由） ----------
    def _music_client(request: Request, platform: str):
        client = request.app.state.clients.get(platform)
        if client is None or platform not in PLATFORMS:
            raise HTTPException(400, f"未知或未接入的音乐平台：{platform}")
        return client

    @router.get("/music/platforms")
    async def music_platforms(request: Request):
        st = request.app.state
        accounts = await st.storage.list_music_accounts()
        return {"platforms": platform_overviews(accounts, set(st.clients))}

    @router.get("/music/{platform}/qr")
    async def music_qr_start(request: Request, platform: str):
        client = _music_client(request, platform)
        try:
            return await client.qr_start(
                login_type=request.query_params.get("type", "")
            )
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"获取二维码失败：{e}") from e

    @router.get("/music/{platform}/qr/check")
    async def music_qr_check(request: Request, platform: str, key: str):
        st = request.app.state
        client = _music_client(request, platform)
        try:
            result = await client.qr_check(key)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"检查扫码状态失败：{e}") from e
        if result.get("state") == QR_DONE:
            cookie = _clean_cookie(result.get("cookie") or "")
            client.set_cookie(cookie)
            info = await client.login_status()
            await st.storage.save_music_account(
                platform,
                cookie_enc=st.box.encrypt(cookie),
                uid=info["uid"], nickname=info["nickname"], status="ok",
            )
            return {"state": QR_DONE, "logged_in": True,
                    "nickname": info["nickname"]}
        return {"state": result.get("state"), "logged_in": False,
                "message": result.get("message", "")}

    @router.post("/music/{platform}/cookie")
    async def music_cookie(request: Request, platform: str, body: CookieIn):
        st = request.app.state
        client = _music_client(request, platform)
        meta = PLATFORMS[platform]
        cookie = _clean_cookie(body.cookie)
        if meta.cookie_field and meta.cookie_field not in cookie:
            raise HTTPException(400, f"登录态中需包含 {meta.cookie_field} 字段")
        client.set_cookie(cookie)
        try:
            info = await client.login_status()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"验证登录失败：{e}") from e
        if not info["logged_in"]:
            raise HTTPException(400, "登录态无效或已过期")
        await st.storage.save_music_account(
            platform,
            cookie_enc=st.box.encrypt(cookie),
            uid=info["uid"], nickname=info["nickname"], status="ok",
        )
        return {"ok": True, **info}

    @router.get("/music/{platform}/status")
    async def music_status(request: Request, platform: str):
        st = request.app.state
        client = _music_client(request, platform)
        acc = await st.storage.get_music_account(platform)
        if not acc or not acc.get("cookie_enc"):
            return {"logged_in": False}
        client.set_cookie(st.box.decrypt(acc["cookie_enc"]))
        try:
            info = await client.login_status()
        except Exception:  # noqa: BLE001
            return {"logged_in": False, "error": f"{PLATFORMS[platform].label}协议服务不可达"}
        if info["logged_in"]:
            # 顺带补全 uid/nickname（迁移或异常登录可能缺失）
            await st.storage.save_music_account(
                platform, uid=info["uid"], nickname=info["nickname"], status="ok"
            )
        else:
            await st.storage.save_music_account(platform, status="expired")
        return info

    @router.get("/music/{platform}/playlists")
    async def music_playlists(request: Request, platform: str):
        st = request.app.state
        client = _music_client(request, platform)
        acc = await st.storage.get_music_account(platform)
        if not acc or not acc.get("uid"):
            raise HTTPException(400, f"请先登录{PLATFORMS[platform].label}")
        client.set_cookie(st.box.decrypt(acc["cookie_enc"]))
        try:
            playlists = await client.user_playlists(acc["uid"])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"拉取歌单失败：{e}") from e
        return {"playlists": playlists}

    # ---------- 歌单 ----------
    @router.post("/playlists")
    async def playlist_add(request: Request, body: PlaylistIn):
        st = request.app.state
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "歌单名不能为空")
        if body.cron and not croniter.is_valid(body.cron):
            raise HTTPException(400, "cron 表达式不合法（示例：0 4 * * * 每天4点）")
        if await st.storage.get_playlist_by_name(name):
            raise HTTPException(400, "同名歌单已存在")
        remote_id = body.remote_id or body.netease_id
        source = body.source
        if remote_id and source == "local":
            source = "netease"  # 旧前端只传 netease_id 的兼容
        if source != "local":
            if st.settings.desktop:
                raise HTTPException(400, "桌面版不支持音乐平台歌单，请使用本地歌单或在线音源")
            if source not in PLATFORMS:
                raise HTTPException(400, f"未知歌单来源：{source}")
            if not remote_id:
                raise HTTPException(400, "请填写远端歌单 ID")
            folder = playlist_folder(st.settings.music_dir, source, name)
        else:
            try:
                base = resolve_local_folder(
                    st.settings.music_dir, st.settings.music_host_dir, body.folder)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            folder = base if body.folder.strip() else base / sanitize_filename(name)
        folder.mkdir(parents=True, exist_ok=True)
        pid = await st.storage.add_playlist(
            name=name, source=source, remote_id=remote_id,
            folder=str(folder), cron=body.cron, remark=body.remark.strip(),
        )
        # 远端歌单添加后自动同步，免去再点一次"立即同步"
        started = False
        if source != "local":
            st.sync.start(pid)
            started = True
        return {"id": pid, "sync_started": started}

    @router.post("/playlists/{pid}/meta")
    async def playlist_meta(request: Request, pid: int, body: PlaylistMetaIn):
        st = request.app.state
        pl = await st.storage.get_playlist(pid)
        if not pl:
            raise HTTPException(404, "歌单不存在")
        fields = {}
        if body.name is not None:
            name = body.name.strip()
            if not name:
                raise HTTPException(400, "歌单名不能为空")
            if name != pl["name"]:
                dup = await st.storage.get_playlist_by_name(name)
                if dup:
                    raise HTTPException(400, "同名歌单已存在")
                fields["name"] = name
        if body.remark is not None:
            fields["remark"] = body.remark.strip()[:60]
        if fields:
            await st.storage.update_playlist(pid, **fields)
        return {"ok": True, **fields}

    @router.post("/playlists/{pid}/sync")
    async def playlist_sync(request: Request, pid: int):
        st = request.app.state
        pl = await st.storage.get_playlist(pid)
        if not pl or pl["source"] not in st.clients:
            raise HTTPException(400, "只有已接入平台的远端歌单可以同步")
        prog = st.sync.progress.get(pid)
        if prog and prog["state"] == "running":
            raise HTTPException(409, "正在同步中")
        st.sync.start(pid)
        return {"started": True}

    @router.get("/playlists/{pid}/progress")
    async def playlist_progress(request: Request, pid: int):
        return request.app.state.sync.progress.get(pid, {})

    @router.post("/playlists/{pid}/cron")
    async def playlist_cron(request: Request, pid: int, body: CronIn):
        if body.cron and not croniter.is_valid(body.cron):
            raise HTTPException(400, "cron 表达式不合法")
        await request.app.state.storage.update_playlist(pid, cron=body.cron)
        return {"ok": True}

    @router.delete("/playlists/{pid}")
    async def playlist_delete(request: Request, pid: int):
        await request.app.state.storage.delete_playlist(pid)
        return {"ok": True, "note": "已删除索引，音频文件仍保留在磁盘"}

    @router.post("/library/scan")
    async def library_scan(request: Request):
        st = request.app.state
        r = await scan_library(st.storage, st.settings.music_dir,
                               include_video=st.settings.desktop)
        # 扫描后把需要转码的高解析曲目排入后台预转码队列
        try:
            await st.transcode.enqueue_hi_res()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("扫描后预转码排队失败", exc_info=True)
        return r

    # ---------- 在线音源（直推 URL 给音箱，不经本地媒体流） ----------
    ONLINE_PLAYLIST = "在线音源"

    async def _online_playlist_id(st) -> int:
        pl = await st.storage.get_playlist_by_name(ONLINE_PLAYLIST)
        if pl:
            return pl["id"]
        return await st.storage.add_playlist(
            name=ONLINE_PLAYLIST, source="online",
            remark="网络电台 / 在线音频直链",
        )

    @router.get("/online")
    async def online_list(request: Request):
        st = request.app.state
        pl = await st.storage.get_playlist_by_name(ONLINE_PLAYLIST)
        tracks = await st.storage.tracks_by_playlist(pl["id"]) if pl else []
        return {"tracks": [t for t in tracks if t["source"] == "online"]}

    @router.post("/online")
    async def online_add(request: Request, body: OnlineIn):
        st = request.app.state
        title = body.title.strip()
        url = body.url.strip()
        if not title:
            raise HTTPException(400, "名称不能为空")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "音源地址必须是 http:// 或 https:// 直链")
        if await st.storage.get_track_by_path(url):
            raise HTTPException(400, "该地址已添加")
        pid = await _online_playlist_id(st)
        tid = await st.storage.upsert_track(
            title=title, path=url, duration=max(0.0, body.duration),
            playlist_id=pid, source="online",
        )
        return {"id": tid}

    @router.delete("/online/{tid}")
    async def online_delete(request: Request, tid: int):
        st = request.app.state
        track = await st.storage.get_track(tid)
        if not track or track["source"] != "online":
            raise HTTPException(404, "在线音源不存在")
        await st.storage.delete_track(tid)
        return {"ok": True}

    # ---------- 桌面版配置（desktop.json，保存后重启生效） ----------
    def _require_desktop(request: Request):
        if not request.app.state.settings.desktop:
            raise HTTPException(404, "仅桌面版支持")

    @router.get("/desktop/config")
    async def desktop_config_get(request: Request):
        _require_desktop(request)
        s = request.app.state.settings
        return {"music_dir": str(s.music_dir), "port": s.port,
                "public_url": s.public_url,
                "speaker_base_url": s.speaker_base_url()}

    @router.post("/desktop/config")
    async def desktop_config_set(request: Request, body: DesktopConfigIn):
        _require_desktop(request)
        from ..desktop import load_desktop_config, save_desktop_config

        s = request.app.state.settings
        cfg = load_desktop_config(s.data_dir)
        if body.music_dir is not None:
            p = Path(body.music_dir.strip()).expanduser()
            if not p.is_absolute():
                raise HTTPException(400, "媒体目录必须是绝对路径")
            try:
                p.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(400, f"目录不可用：{e}") from e
            cfg["music_dir"] = str(p)
        if body.port is not None:
            if not (1024 <= body.port <= 65535):
                raise HTTPException(400, "端口范围 1024–65535")
            cfg["port"] = body.port
        if body.public_url is not None:
            u = body.public_url.strip().rstrip("/")
            if u and not u.startswith(("http://", "https://")):
                raise HTTPException(400, "公网/局域网地址需以 http:// 开头")
            cfg["public_url"] = u
        save_desktop_config(s.data_dir, cfg)
        return {"ok": True, "restart_required": True}

    @router.post("/desktop/restart")
    async def desktop_restart(request: Request):
        _require_desktop(request)
        from ..desktop import schedule_restart

        schedule_restart()
        return {"ok": True}

    @router.post("/desktop/shutdown")
    async def desktop_shutdown(request: Request):
        _require_desktop(request)
        from ..desktop import schedule_shutdown

        schedule_shutdown()
        return {"ok": True}

    # ---------- LLM 服务商 ----------
    @router.get("/providers")
    async def providers_list(request: Request):
        providers = await request.app.state.storage.list_providers()
        return {"providers": [_mask_provider(p) for p in providers],
                "presets": PRESETS}

    @router.post("/providers")
    async def provider_add(request: Request, body: ProviderIn):
        st = request.app.state
        pid = await st.storage.add_provider(
            preset=body.preset, name=body.name, base_url=body.base_url.rstrip("/"),
            api_key_enc=st.box.encrypt(_extract_web_key(body.preset, body.api_key)),
            model=body.model, priority=body.priority, enabled=body.enabled,
            extra_json=_merge_extra(None, body.uskey, body.agent_id),
        )
        return {"id": pid}

    @router.put("/providers/{pid}")
    async def provider_update(request: Request, pid: int, body: ProviderUpdate):
        st = request.app.state
        fields = body.model_dump(exclude_none=True)
        uskey = fields.pop("uskey", None)
        agent_id = fields.pop("agent_id", None)
        if "api_key" in fields:
            preset = fields.get("preset")
            if not preset:
                existing = await st.storage.get_provider(pid)
                preset = existing["preset"] if existing else "custom"
            fields["api_key_enc"] = st.box.encrypt(
                _extract_web_key(preset, fields.pop("api_key"))
            )
        if "base_url" in fields:
            fields["base_url"] = fields["base_url"].rstrip("/")
        if uskey is not None or agent_id is not None:
            existing = await st.storage.get_provider(pid)
            fields["extra_json"] = _merge_extra(
                existing.get("extra_json") if existing else None, uskey, agent_id
            )
        await st.storage.update_provider(pid, **fields)
        return {"ok": True}

    @router.delete("/providers/{pid}")
    async def provider_delete(request: Request, pid: int):
        await request.app.state.storage.delete_provider(pid)
        return {"ok": True}

    @router.post("/providers/{pid}/test")
    async def provider_test(request: Request, pid: int):
        st = request.app.state
        p = await st.storage.get_provider(pid)
        if not p:
            raise HTTPException(404, "服务商不存在")
        preview = ""
        messages = [{"role": "user", "content": "你好，请用一句话介绍你自己"}]
        try:
            if p["preset"] == "doubao-web":
                from ..llm.doubao import stream_chat_doubao

                stream = stream_chat_doubao(
                    st.box.decrypt(p["api_key_enc"]), messages, timeout=30.0
                )
            elif p["preset"] == "yuanbao-web":
                from ..llm.yuanbao import DEFAULT_AGENT_ID, stream_chat_yuanbao

                extra = json.loads(p.get("extra_json") or "{}")
                stream = stream_chat_yuanbao(
                    cookie=st.box.decrypt(p["api_key_enc"]),
                    uskey=extra.get("uskey", ""),
                    model=p["model"], messages=messages, timeout=30.0,
                    agent_id=extra.get("agent_id") or DEFAULT_AGENT_ID,
                )
            else:
                stream = stream_chat(
                    p["base_url"], st.box.decrypt(p["api_key_enc"]), p["model"],
                    messages, timeout=25.0,
                )
            async for delta in stream:
                preview += delta
                if len(preview) >= 80:
                    break
            return {"ok": bool(preview.strip()), "preview": preview[:120]}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:300]}

    # ---------- 设置 / 对话 ----------
    @router.get("/settings")
    async def settings_get(request: Request):
        settings = await request.app.state.storage.all_settings()
        settings.pop("admin_hash", None)
        settings.pop("admin_salt", None)
        return settings

    @router.post("/settings")
    async def settings_set(request: Request, body: SettingIn):
        if body.key not in DEFAULT_RUNTIME_SETTINGS:
            raise HTTPException(400, "不支持的设置项")
        await request.app.state.storage.set_setting(body.key, body.value)
        return {"ok": True}

    @router.post("/password")
    async def password_change(request: Request, body: PasswordIn):
        if len(body.new) < 6:
            raise HTTPException(400, "新密码至少 6 位")
        ok = await change_password(request.app.state.storage, body.old, body.new)
        if not ok:
            raise HTTPException(400, "原密码错误")
        return {"ok": True}

    @router.get("/chat/logs")
    async def chat_logs(request: Request, did: str | None = None, limit: int = 100):
        return {"logs": await request.app.state.storage.recent_chat_logs(did, limit)}

    # ---------- 出声链路直测（管理员鉴权；用于部署验证） ----------
    async def _get_speaker(request: Request, did: str):
        st = request.app.state
        if not st.manager.ready:
            raise HTTPException(400, "小米账号未登录")
        dev = await st.storage.get_device(did)
        if not dev:
            raise HTTPException(404, "设备不存在，请先刷新设备列表")
        from ..xiaomi.speaker import Speaker

        return Speaker(st.manager.mina, did, dev["name"], dev["hardware"],
                       manager=st.manager)

    @router.post("/test/tts")
    async def test_tts(request: Request, body: TtsTest):
        speaker = await _get_speaker(request, body.did)
        ok = await speaker.tts(body.text[:200])
        return {"ok": ok}

    @router.post("/test/play")
    async def test_play(request: Request, body: PlayTest):
        st = request.app.state
        speaker = await _get_speaker(request, body.did)
        track = await st.storage.get_track(body.track_id)
        if not track:
            raise HTTPException(404, "曲目不存在")
        url = track_play_url(st.settings, track)
        ok = await speaker.play_url(url)
        return {"ok": ok, "url": url, "title": track["title"]}

    @router.post("/chat/clear")
    async def chat_clear(request: Request, body: ChatClear):
        await request.app.state.storage.clear_chat_logs(body.did)
        return {"ok": True}

    # ---------- 播放控制（Web/APP 遥控器） ----------
    @router.get("/player/state")
    async def player_state(request: Request, did: str):
        st = request.app.state
        speaker = await _get_speaker(request, did)
        status = await speaker.play_status() or {}
        dev = await st.storage.get_device(did) or {}
        cfg = json.loads(dev.get("config_json") or "{}")
        return {
            "now_playing": st.music.now_playing(did),
            "volume": status.get("volume"),
            "device": did,
            "mode": cfg.get("mode", "wushuang"),
            "xiaomi_status": (await st.manager.status()).get("status"),
        }

    @router.post("/player/mode")
    async def player_mode(request: Request, body: PlayerModeIn):
        if body.mode not in ("wushuang", "native"):
            raise HTTPException(400, "mode 只能是 wushuang 或 native")
        st = request.app.state
        dev = await st.storage.get_device(body.did)
        if not dev:
            raise HTTPException(404, "设备不存在")
        cfg = json.loads(dev.get("config_json") or "{}")
        cfg["mode"] = body.mode
        await st.storage.set_device_config(body.did, cfg)
        return {"ok": True, "mode": body.mode}

    @router.post("/player/play")
    async def player_play(request: Request, body: PlayerPlayIn):
        st = request.app.state
        speaker = await _get_speaker(request, body.did)
        if body.track_id is not None:
            track = await st.storage.get_track(body.track_id)
            if not track:
                raise HTTPException(404, "曲目不存在")
            tracks, name = [track], f"点歌：{track['title']}"
        elif body.playlist_id is not None:
            pl = await st.storage.get_playlist(body.playlist_id)
            if not pl:
                raise HTTPException(404, "歌单不存在")
            tracks = await st.storage.tracks_by_playlist(pl["id"])
            name = pl["name"]
            if not tracks:
                raise HTTPException(400, "歌单为空，请先同步或扫描曲库")
        else:
            raise HTTPException(400, "需要 track_id 或 playlist_id")
        ok = await st.music.play_queue(speaker, tracks, name)
        return {"ok": ok, "count": len(tracks), "playlist": name}

    @router.post("/player/control")
    async def player_control(request: Request, body: PlayerControlIn):
        if body.action not in _PLAYER_ACTIONS:
            raise HTTPException(400, f"未知控制动作：{body.action}")
        st = request.app.state
        speaker = await _get_speaker(request, body.did)
        from ..dispatcher import Intent
        await st.music.handle(speaker, Intent("control", action=body.action,
                                              value=body.value), {})
        return {"ok": True, "now_playing": st.music.now_playing(body.did)}

    @router.post("/player/native")
    async def player_native(request: Request, body: PlayerNativeIn):
        text = body.text.strip()[:100]
        if not text:
            raise HTTPException(400, "指令不能为空")
        speaker = await _get_speaker(request, body.did)
        ok = await speaker.nlp(text)
        return {"ok": ok}

    @router.get("/player/search")
    async def player_search(request: Request, q: str = "", limit: int = 30):
        if not q.strip():
            return {"tracks": []}
        tracks = await request.app.state.storage.search_tracks(q.strip(),
                                                              limit=min(limit, 100))
        return {"tracks": tracks}

    @router.get("/player/tracks")
    async def player_tracks(request: Request, playlist_id: int | None = None,
                            page: int = 1, page_size: int = 50):
        rows, total = await request.app.state.storage.tracks_page(
            playlist_id, page, min(page_size, 200))
        return {"tracks": rows, "total": total, "page": page}

    @router.get("/player/playlists")
    async def player_playlists(request: Request):
        st = request.app.state
        counts = await st.storage.playlist_track_counts()
        pls = await st.storage.list_playlists()
        return {"playlists": [
            {"id": p["id"], "name": p["name"], "source": p["source"],
             "remark": p.get("remark") or "",
             "count": counts.get(p["id"], 0)} for p in pls
        ]}

    # ---------- 本地曲库浏览 / 播放全部 / 歌单批量管理 ----------
    _GROUP_LABELS = {"artist": "歌手", "album": "专辑", "folder": "文件夹"}

    async def _group_tracks(st, kind: str, name: str) -> list[dict]:
        name = (name or "").strip()
        if kind not in _GROUP_LABELS or not name:
            raise HTTPException(400, "分组参数不合法")
        if kind == "artist":
            return await st.storage.tracks_by_artist(name)
        if kind == "album":
            return await st.storage.tracks_by_album(name)
        return await st.storage.tracks_by_folder(name)

    @router.get("/library/browse")
    async def library_browse(request: Request, mode: str = "all", page: int = 1,
                             page_size: int = 50, folder: str = "", album: str = "",
                             artist: str = "", playlist_id: int | None = None):
        st = request.app.state
        if mode == "playlist":
            if playlist_id is None:
                raise HTTPException(400, "缺少 playlist_id")
            rows, total = await st.storage.tracks_page(playlist_id, page, min(page_size, 200))
            return {"mode": mode, "tracks": rows, "total": total, "page": page}
        if mode == "folder" and not folder:
            return {"mode": mode, "groups": await st.storage.local_folders()}
        if mode == "album" and not album:
            return {"mode": mode, "groups": await st.storage.local_albums()}
        if mode == "artist" and not artist:
            return {"mode": mode, "groups": await st.storage.local_artists()}
        rows, total = await st.storage.local_tracks_page(
            page, min(page_size, 200), folder or None, album or None, artist or None)
        return {"mode": mode, "tracks": rows, "total": total, "page": page}

    @router.get("/library/search")
    async def library_search(request: Request, q: str = "", limit: int = 100):
        if not q.strip():
            return {"tracks": [], "total": 0}
        rows = await request.app.state.storage.local_search(q.strip(), min(limit, 200))
        return {"tracks": rows, "total": len(rows)}

    @router.post("/library/play_artist")
    async def library_play_artist(request: Request, body: PlayAllIn, artist: str = ""):
        st = request.app.state
        if not artist.strip():
            raise HTTPException(400, "缺少歌手名")
        speaker = await _get_speaker(request, body.did)
        tracks = await st.storage.tracks_by_artist(artist.strip())
        if not tracks:
            raise HTTPException(400, "该歌手没有本地歌曲")
        ok = await st.music.play_queue(speaker, tracks, f"歌手：{artist.strip()}")
        if body.mode == "shuffle":
            st.music._states[body.did].mode = "shuffle"
        return {"ok": ok, "count": len(tracks)}

    @router.post("/library/play_all")
    async def library_play_all(request: Request, body: PlayAllIn):
        st = request.app.state
        speaker = await _get_speaker(request, body.did)
        tracks = await st.storage.all_local_tracks()
        if not tracks:
            raise HTTPException(400, "本地曲库为空，请先扫描")
        ok = await st.music.play_queue(speaker, tracks, "全部本地歌曲")
        if body.mode == "shuffle":
            st.music._states[body.did].mode = "shuffle"
        return {"ok": ok, "count": len(tracks)}

    @router.post("/library/play_group")
    async def library_play_group(request: Request, body: PlayGroupIn):
        """一键播放分组（歌手/专辑/文件夹）的全部本地歌曲。"""
        st = request.app.state
        tracks = await _group_tracks(st, body.kind, body.name)
        if not tracks:
            raise HTTPException(400, "该分组没有本地歌曲")
        speaker = await _get_speaker(request, body.did)
        label = _GROUP_LABELS[body.kind]
        ok = await st.music.play_queue(
            speaker, tracks, f"{label}：{body.name.strip()}")
        if body.mode == "shuffle":
            st.music._states[body.did].mode = "shuffle"
        return {"ok": ok, "count": len(tracks)}

    @router.post("/library/make_playlist")
    async def library_make_playlist(request: Request, body: MakePlaylistIn):
        """一键生成与分组同名的歌单并收录其全部歌曲。

        幂等：同名歌单已存在时补齐成员（不报错、不重复），可放心重复点击。
        成员关系为多对多：不影响这些歌曲原有的歌单归属。
        """
        st = request.app.state
        tracks = await _group_tracks(st, body.kind, body.name)
        if not tracks:
            raise HTTPException(400, "该分组没有本地歌曲，无法生成歌单")
        raw = body.name.strip()
        name = (raw.replace("\\", "/").rstrip("/").split("/")[-1]
                if body.kind == "folder" else raw)
        if not name:
            raise HTTPException(400, "分组名不能为空")
        pl = await st.storage.get_playlist_by_name(name)
        created = False
        if pl:
            pid = pl["id"]
        else:
            folder = st.settings.music_dir / "local" / sanitize_filename(name)
            folder.mkdir(parents=True, exist_ok=True)
            remark = body.remark.strip() or (
                raw if body.kind == "folder" else f"{_GROUP_LABELS[body.kind]}：{raw}")
            pid = await st.storage.add_playlist(
                name=name, source="local", folder=str(folder), remark=remark[:60])
            created = True
        added = await st.storage.playlist_add_tracks(pid, [t["id"] for t in tracks])
        return {"id": pid, "name": name, "created": created,
                "added": added, "total": len(tracks)}

    @router.post("/playlists/{pid}/tracks")
    async def playlist_tracks_add(request: Request, pid: int, body: PlaylistTracksIn):
        st = request.app.state
        if not await st.storage.get_playlist(pid):
            raise HTTPException(404, "歌单不存在")
        n = await st.storage.playlist_add_tracks(pid, body.track_ids)
        return {"ok": True, "changed": n}

    @router.delete("/playlists/{pid}/tracks")
    async def playlist_tracks_remove(request: Request, pid: int, body: PlaylistTracksIn):
        st = request.app.state
        if not await st.storage.get_playlist(pid):
            raise HTTPException(404, "歌单不存在")
        n = await st.storage.playlist_remove_tracks(pid, body.track_ids)
        return {"ok": True, "changed": n}

    return router
