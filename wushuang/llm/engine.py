"""对话编排：历史记忆 → 多服务商降级 → 流式分句 → 小爱原声播报。

每台音箱同一时刻只有一个回答任务；新问题到来会取消旧任务（打断），
避免两台音箱或多轮提问互相覆盖。播报节奏按字数估算（小爱 TTS 无完成
回调），语速可在后台调整。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from ..crypto import SecretBox
from ..storage import Storage
from ..utils.text import SentenceBuffer
from ..xiaomi.speaker import Speaker
from .client import LLMError, stream_chat
from .doubao import stream_chat_doubao
from .yuanbao import DEFAULT_AGENT_ID, stream_chat_yuanbao

_LOGGER = logging.getLogger(__name__)


class LLMEngine:
    def __init__(self, storage: Storage, box: SecretBox):
        self.storage = storage
        self.box = box
        self._jobs: dict[str, asyncio.Task] = {}

    async def ask(self, speaker: Speaker, query: str, cfg: dict) -> None:
        did = speaker.did
        prev = self._jobs.get(did)
        if prev and not prev.done():
            prev.cancel()
        self._jobs[did] = asyncio.create_task(
            self._answer(speaker, query, cfg), name=f"llm-{did}"
        )

    async def stop(self, did: str) -> None:
        task = self._jobs.get(did)
        if task and not task.done():
            task.cancel()

    async def _build_messages(self, speaker: Speaker, query: str, cfg: dict) -> list[dict]:
        system = cfg.get("system_prompt") or await self.storage.get_setting(
            "llm_system_prompt", ""
        )
        messages = [{"role": "system", "content": system}]
        turns = int(await self.storage.get_setting("llm_history_turns", "6"))
        if turns > 0:
            logs = await self.storage.recent_chat_logs(speaker.did, limit=turns * 2)
            for row in reversed(logs):
                if row["status"] != "ok" or not row["answer"]:
                    continue
                messages.append({"role": "user", "content": row["query"]})
                messages.append({"role": "assistant", "content": row["answer"]})
        messages.append({"role": "user", "content": query})
        return messages

    async def _answer(self, speaker: Speaker, query: str, cfg: dict) -> None:
        t0 = time.monotonic()
        did = speaker.did
        providers = await self.storage.list_providers(enabled_only=True)
        if not providers:
            await speaker.tts("还没有配置大模型服务，请先在无双后台添加。")
            await self.storage.add_chat_log(did, query, "", "", 0, "no_provider")
            return

        if (await self.storage.get_setting("llm_interrupt_playing", "true")) == "true":
            await speaker.pause()

        messages = await self._build_messages(speaker, query, cfg)
        timeout = float(await self.storage.get_setting("llm_timeout", "60"))
        cps = float(await self.storage.get_setting("tts_chars_per_second", "4.5"))
        min_len = int(await self.storage.get_setting("tts_min_chunk", "6"))
        max_len = int(await self.storage.get_setting("tts_max_chunk", "60"))

        full = ""
        used = ""
        last_err: Exception | None = None
        for p in providers:
            buf = SentenceBuffer(min_len, max_len)
            full = ""
            try:
                key = self.box.decrypt(p["api_key_enc"])
                if p["preset"] == "doubao-web":
                    # 内置 2026 新协议客户端（免签名），不再依赖中转容器
                    stream = stream_chat_doubao(key, messages, timeout=timeout)
                elif p["preset"] == "yuanbao-web":
                    extra = json.loads(p.get("extra_json") or "{}")
                    stream = stream_chat_yuanbao(
                        cookie=key,
                        uskey=extra.get("uskey", ""),
                        model=p["model"],
                        messages=messages,
                        timeout=timeout,
                        agent_id=extra.get("agent_id") or DEFAULT_AGENT_ID,
                    )
                else:
                    stream = stream_chat(
                        p["base_url"], key, p["model"], messages, timeout=timeout
                    )
                async for delta in stream:
                    full += delta
                    for sent in buf.feed(delta):
                        await speaker.tts(sent)
                        await asyncio.sleep(len(sent) / cps + 0.3)
                tail = buf.flush()
                if tail:
                    await speaker.tts(tail)
                    await asyncio.sleep(len(tail) / cps + 0.3)
                if not full.strip():
                    raise LLMError("模型返回空回答")
                used = p["name"]
                break
            except asyncio.CancelledError:
                _LOGGER.info("[%s] 回答被新问题打断", speaker.name)
                raise
            except Exception as e:  # noqa: BLE001 - 降级到下一个服务商
                last_err = e
                _LOGGER.warning(
                    "[%s] 服务商 %s 失败，尝试降级: %s", speaker.name, p["name"], e
                )
                continue

        latency = int((time.monotonic() - t0) * 1000)
        status = "ok" if used else "failed"
        await self.storage.add_chat_log(did, query, full, used, latency, status)
        if not used:
            _LOGGER.error("[%s] 所有服务商均失败: %s", speaker.name, last_err)
            await speaker.tts("抱歉，大模型服务暂时不可用，请稍后再试。")
