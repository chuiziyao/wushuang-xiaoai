"""定时同步调度：每 30 秒检查各网易云歌单的 cron 表达式。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from croniter import croniter

from ...storage import Storage
from .sync import NeteaseSync

_LOGGER = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(self, storage: Storage, sync: NeteaseSync):
        self.storage = storage
        self.sync = sync
        self._task: asyncio.Task | None = None
        self._last_check: dict[int, datetime] = {}

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="netease-scheduler")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                # 使用 NAS 本地时间解释 cron（与用户直觉一致）
                now = datetime.now()
                for pl in await self.storage.list_playlists():
                    if pl["source"] != "netease" or not pl["enabled"]:
                        continue
                    cron = (pl.get("cron") or "").strip()
                    if not cron or not croniter.is_valid(cron):
                        continue
                    pid = pl["id"]
                    last = self._last_check.get(pid, now)
                    self._last_check[pid] = now
                    nxt = croniter(cron, last).get_next(datetime)
                    if nxt <= now:
                        prog = self.sync.progress.get(pid)
                        if prog and prog["state"] == "running":
                            continue
                        _LOGGER.info("定时同步歌单 %s", pl["name"])
                        self.sync.start(pid)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.exception("同步调度异常")
