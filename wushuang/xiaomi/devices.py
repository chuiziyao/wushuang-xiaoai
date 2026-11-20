"""设备发现：从小米云拉取音箱列表并登记到数据库。"""

from __future__ import annotations

import logging

from ..storage import Storage
from .account import XiaomiAccountManager

_LOGGER = logging.getLogger(__name__)


async def refresh_devices(manager: XiaomiAccountManager, storage: Storage) -> list[dict]:
    if not manager.ready:
        return await storage.list_devices()
    try:
        devices = await manager.mina.device_list()
    except Exception:  # noqa: BLE001
        _LOGGER.warning("拉取小米设备列表失败", exc_info=True)
        return await storage.list_devices()
    for d in devices or []:
        did = d.get("deviceID") or ""
        if not did:
            continue
        await storage.upsert_device(
            did=did,
            name=d.get("name") or d.get("alias") or did,
            hardware=d.get("hardware") or "",
            model=d.get("model") or "",
        )
    return await storage.list_devices()
