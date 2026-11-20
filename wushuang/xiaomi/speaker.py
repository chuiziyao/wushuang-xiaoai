"""单台音箱的操作封装：对话轮询、TTS、URL 播放、播放控制。

LX06 / S12 均走 MiNAService 的 ubus 通道：
- 对话记录: nlp_result_get (mibrain)
- 播报: text_to_speech (mibrain) —— 小爱原声
- 播放: player_play_url (mediaplayer)，MiService 内部按硬件自动选择
  player_play_music（部分新机型）或 player_play_url。
"""

from __future__ import annotations

import json
import logging
import time

import aiohttp

from ..vendor.miservice import MiNAService

_LOGGER = logging.getLogger(__name__)

# 仅这些型号用 ubus nlp_result_get 查对话（参考 xiaomusic GET_ASK_BY_MINA）
_UBUS_ASK_MODELS = {"M01"}


def _ubus_ok(result) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("code") != 0:
        return False
    data = result.get("data") or {}
    return data.get("code", 0) == 0


class Speaker:
    def __init__(self, mina: MiNAService, did: str, name: str, hardware: str,
                 manager=None):
        self.mina = mina
        self.did = did
        self.name = name
        self.hardware = hardware
        self.manager = manager

    async def latest_asks(self) -> list[dict]:
        """返回最近的用户问句 [{request_id, timestamp_ms, question, answer}]。

        LX06 等主流型号不支持 ubus nlp_result_get，须用对话记录 HTTP API
        （cookie 的 deviceId 必须是音箱 deviceID，而非账号 passport deviceId）。
        """
        if self.hardware not in _UBUS_ASK_MODELS and self.manager is not None:
            try:
                return await self._asks_via_conversation()
            except Exception:  # noqa: BLE001 - 回退 ubus
                _LOGGER.debug("对话API失败，回退ubus did=%s", self.did, exc_info=True)
        try:
            messages = await self.mina.get_latest_ask(self.did)
        except Exception:  # noqa: BLE001 - 轮询容错，下轮重试
            _LOGGER.debug("获取对话记录失败 did=%s", self.did, exc_info=True)
            return []
        out = []
        for msg in messages:
            try:
                answers = msg["response"]["answer"]
                if not answers:
                    continue
                out.append(
                    {
                        "request_id": msg["request_id"],
                        "timestamp_ms": int(msg["timestamp_ms"]),
                        "question": answers[0].get("question", ""),
                        "answer": answers[0].get("content", ""),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return out

    async def _asks_via_conversation(self) -> list[dict]:
        tok = self.manager._token_cache or {}
        sid = tok.get("micoapi")
        session = self.manager._session
        if not sid or session is None or session.closed:
            return []
        ts = int(time.time() * 1000)
        url = (
            "https://userprofile.mina.mi.com/device_profile/v2/conversation"
            f"?source=dialogu&hardware={self.hardware}&timestamp={ts}&limit=5"
        )
        cookies = {
            "deviceId": self.did,
            "serviceToken": sid[1],
            "userId": str(tok.get("userId", "")),
        }
        async with session.get(
            url, cookies=cookies, timeout=aiohttp.ClientTimeout(total=15)
        ) as r:
            obj = await r.json(content_type=None)
        if obj.get("code") != 0:
            return []
        data = json.loads(obj.get("data") or "{}")
        out = []
        for rec in data.get("records") or []:
            answer = ""
            for a in rec.get("answers") or []:
                if a.get("type") == "TTS":
                    answer = (a.get("tts") or {}).get("text", "")
                    break
            out.append(
                {
                    "request_id": rec.get("requestId", ""),
                    "timestamp_ms": int(rec.get("time", 0)),
                    "question": rec.get("query", ""),
                    "answer": answer,
                }
            )
        return out

    async def tts(self, text: str) -> bool:
        if not text:
            return True
        try:
            return _ubus_ok(await self.mina.text_to_speech(self.did, text))
        except Exception:  # noqa: BLE001
            _LOGGER.warning("TTS 失败 did=%s text=%s", self.did, text[:30], exc_info=True)
            return False

    async def play_url(self, url: str) -> bool:
        try:
            return _ubus_ok(await self.mina.play_by_url(self.did, url))
        except Exception:  # noqa: BLE001
            _LOGGER.warning("播放 URL 失败 did=%s url=%s", self.did, url, exc_info=True)
            return False

    async def pause(self) -> bool:
        try:
            return _ubus_ok(await self.mina.player_pause(self.did))
        except Exception:  # noqa: BLE001
            return False

    async def resume(self) -> bool:
        try:
            return _ubus_ok(await self.mina.player_play(self.did))
        except Exception:  # noqa: BLE001
            return False

    async def stop(self) -> bool:
        try:
            return _ubus_ok(await self.mina.player_stop(self.did))
        except Exception:  # noqa: BLE001
            return False

    async def set_volume(self, volume: int) -> bool:
        volume = max(0, min(100, int(volume)))
        try:
            return _ubus_ok(await self.mina.player_set_volume(self.did, volume))
        except Exception:  # noqa: BLE001
            return False

    async def play_status(self) -> dict | None:
        """返回 {status, volume, ...}；不同机型字段有差异，尽力解析。"""
        try:
            result = await self.mina.player_get_status(self.did)
            if not _ubus_ok(result):
                return None
            info = result["data"].get("info")
            if isinstance(info, str):
                info = json.loads(info)
            return info if isinstance(info, dict) else None
        except Exception:  # noqa: BLE001
            return None
