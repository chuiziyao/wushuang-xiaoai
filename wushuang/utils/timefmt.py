"""时间显示工具：存储统一 UTC，显示/定时按配置时区转换。

数据库里的时间戳是带 +00:00 的 ISO 字符串（storage._now）。
页面展示与 cron 解释统一走 WUSHUANG_TZ（默认 Asia/Shanghai），
避免 Docker 容器默认 UTC 导致页面时间、定时同步差 8 小时。
Windows 与精简版 Linux 镜像没有系统 tzdata，由 pip 依赖 tzdata 提供。
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Asia/Shanghai"


def display_tz() -> ZoneInfo:
    """当前配置时区（环境变量 WUSHUANG_TZ，默认 Asia/Shanghai）。"""
    name = (os.environ.get("WUSHUANG_TZ") or DEFAULT_TZ).strip() or DEFAULT_TZ
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - 配错时区名时回退默认，不让页面崩
        return ZoneInfo(DEFAULT_TZ)


def parse_ts(ts: str | None) -> datetime | None:
    """解析存储层 ISO 时间戳；无时区信息的旧数据按 UTC 处理。"""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def fmt_ts(ts: str | None, fmt: str = "%Y-%m-%d %H:%M:%S", default: str = "—") -> str:
    """UTC 时间戳 → 配置时区的显示字符串（Jinja 过滤器 localtime）。"""
    dt = parse_ts(ts)
    if dt is None:
        return default
    return dt.astimezone(display_tz()).strftime(fmt)
