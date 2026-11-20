"""音乐平台注册表与统一客户端接口约定。

每个平台客户端（NeteasePlatform / KugouClient / QQClient / QishuiClient）
实现以下方法，返回值一律为归一化结构，同步引擎与 Web API 不感知平台差异：

- set_cookie(cookie) / has_cookie：登录态（Cookie 或序列化凭据）
- login_status() -> {logged_in, uid, nickname}
- qr_start() -> {key, qrimg, qrurl}
- qr_check(key) -> {state: waiting|scanned|done|expired, cookie?, message}
- user_playlists(uid) -> [{id, name, trackCount}]
- playlist_tracks(remote_id) -> [{id, name, artist, album}]
- song_urls(ids, level) -> {id: {url, type, br}}（url 为 None 表示无版权/需更高权益）
- close()

协议细节由各 sidecar / 协议库承担，本层只做 HTTP 调用与归一化。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformMeta:
    name: str
    label: str
    scan_app: str  # 用哪个 APP 扫码
    cookie_field: str  # 粘贴登录态时必须包含的字段
    cookie_hint: str
    default_level: str  # 默认下载音质


PLATFORMS: dict[str, PlatformMeta] = {
    "netease": PlatformMeta(
        "netease", "网易云音乐", "网易云音乐 APP", "MUSIC_U",
        "浏览器登录 music.163.com 后 F12 复制，需含 MUSIC_U", "exhigh",
    ),
    "kugou": PlatformMeta(
        "kugou", "酷狗音乐", "酷狗音乐 APP", "token",
        "浏览器登录 kugou.com 后 F12 复制，需含 token", "320",
    ),
    "qq": PlatformMeta(
        "qq", "QQ音乐", "QQ 或 微信 APP", "musickey",
        "扫码登录后导出的凭据，需含 musicid 与 musickey", "320",
    ),
    "qishui": PlatformMeta(
        "qishui", "汽水音乐", "抖音 APP", "sessionid",
        "浏览器登录 qishui.douyin.com 后 F12 复制，需含 sessionid", "default",
    ),
}

# 归一化扫码状态
QR_WAITING = "waiting"  # 等待扫码
QR_SCANNED = "scanned"  # 已扫码，待手机确认
QR_DONE = "done"  # 登录成功（带 cookie）
QR_EXPIRED = "expired"  # 二维码过期


def platform_overviews(accounts: dict, ready: set[str]) -> list[dict]:
    """各平台的公开概览（账号状态 + 是否已接入），供 API 与页面共用。"""
    out = []
    for name, meta in PLATFORMS.items():
        acc = accounts.get(name) or {}
        out.append({
            "name": name, "label": meta.label, "scan_app": meta.scan_app,
            "cookie_field": meta.cookie_field, "cookie_hint": meta.cookie_hint,
            "ready": name in ready,
            "logged_in": acc.get("status") == "ok",
            "nickname": acc.get("nickname") or "",
            "status": acc.get("status") or "logged_out",
        })
    return out
