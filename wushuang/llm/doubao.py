"""豆包网页版内置客户端（2026 chat_ability 新协议，免签名）。

协议来源：tools-doubao / doubao2api 等公开逆向项目 2026 年抓包结论：
- POST https://www.doubao.com/chat/completion?aid=497858...（免 a_bogus 签名）
- Cookie 只需 sessionid（msToken 留空，假值反而触发风控）
- SSE 事件：SSE_ACK / CHUNK_DELTA(json.text 增量) / STREAM_CHUNK / SSE_REPLY_END

仅限个人自用，违反平台条款风险自负。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator
from urllib.parse import urlencode

import httpx

from .client import LLMError

_LOGGER = logging.getLogger(__name__)

BASE = "https://www.doubao.com"
_FP = "verify_mlcfw5f7_TPq0YmFD_NrsC_4RuQ_BJPg_M5W7i58I7wV0"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
_BOT_ID = "7338286299411103781"


def _params() -> dict:
    return {
        "aid": "497858",
        "real_aid": "497858",
        "device_id": "714003710229497",
        "tea_uuid": "714003710229497",
        "web_id": "7604137868021548590",
        "device_platform": "web",
        "language": "zh",
        "region": "CN",
        "sys_region": "CN",
        "pkg_type": "release_version",
        "runtime": "web",
        "runtime_version": "3.5.4",
        "samantha_web": "1",
        "use-olympus-account": "1",
        "fp": _FP,
        "web_tab_id": str(uuid.uuid4()),
        "doubao_pc_version": "3.41.4",
        "pc_version": "3.41.4",
        "version_code": "341400",
        "client_platform": "web",
    }


def _flatten(messages: list[dict]) -> str:
    parts = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if role == "system":
            parts.append(f"[系统指令]\n{content}")
        elif role == "assistant":
            parts.append(f"助手: {content}")
        else:
            parts.append(f"用户: {content}")
    return "\n\n".join(parts)


def _body(prompt: str) -> dict:
    return {
        "client_meta": {
            "local_conversation_id": f"local_{uuid.uuid4().hex[:16]}",
            "conversation_id": "",
            "bot_id": _BOT_ID,
            "last_section_id": "",
            "last_message_index": 0,
        },
        "messages": [
            {
                "local_message_id": str(uuid.uuid4()),
                "content_block": [
                    {
                        "block_type": 10000,
                        "content": {
                            "text_block": {"text": prompt, "icon_url": "",
                                           "icon_url_dark": "", "summary": ""},
                            "pc_event_block": "",
                        },
                        "block_id": str(uuid.uuid4()),
                        "parent_id": "",
                        "meta_info": [],
                        "append_fields": [],
                        "is_finish": True,
                        "patch_type": 2,
                    }
                ],
                "message_status": 0,
            }
        ],
        "option": {
            "send_message_scene": "",
            "create_time_ms": 0,
            "collect_id": "",
            "is_audio": False,
            "answer_with_suggest": True,
            "tts_switch": False,
            "need_deep_think": 0,
            "click_clear_context": False,
            "from_suggest": False,
            "is_regen": False,
            "is_replace": False,
            "disable_sse_cache": False,
            "select_text_action": "",
            "resend_for_regen": False,
            "scene_type": 0,
            "unique_key": str(uuid.uuid4()),
            "start_seq": 0,
            "need_create_conversation": True,
            "conversation_init_option": {"need_ack_conversation": True},
            "conversation_mode": 1,
            "message_storage_type": 0,
            "memory_type": 0,
            "regen_query_id": [],
            "edit_query_id": [],
            "regen_instruction": "",
            "no_replace_for_regen": False,
            "message_from": 0,
            "shared_app_name": "",
            "action_bar_skill_id": 0,
            "sse_recv_event_options": {"support_chunk_delta": True},
            "is_ai_playground": False,
        },
        "ext": {
            "use_deep_think": "0",
            "fp": _FP,
            "use_submit_pipeline": "1",
            "commerce_credit_config_enable": "0",
            "sub_conv_firstmet_type": "1",
        },
    }


async def stream_chat_doubao(
    sessionid: str,
    messages: list[dict],
    timeout: float = 90.0,
    passport_csrf: str = "",
    ttwid: str = "",
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[str]:
    cookie = f"sessionid={sessionid}"
    if ttwid:
        cookie += f"; ttwid={ttwid}"
    if passport_csrf:
        cookie += f"; passport_csrf_token={passport_csrf}"
    headers = {
        "User-Agent": _UA,
        "Cookie": cookie,
        "x-tt-passport-csrf-token": passport_csrf,
        "Origin": BASE,
        "Referer": f"{BASE}/chat",
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "Agw-Js-Conv": "str",
    }
    url = f"{BASE}/chat/completion?{urlencode(_params())}"
    prompt = _flatten(messages)
    got_text = False
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0), transport=transport
        ) as client:
            async with client.stream("POST", url, json=_body(prompt),
                                     headers=headers) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"豆包 HTTP {resp.status_code}: {detail[:200]}")
                event = "message"
                async for line in resp.aiter_lines():
                    line = line.rstrip("\r")
                    if line.startswith("event:"):
                        event = line[6:].strip() or "message"
                        continue
                    if not line.startswith("data:"):
                        if not line.strip():
                            event = "message"
                        continue
                    data = line[5:].lstrip()
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event == "gateway-error" or (
                        isinstance(obj, dict) and obj.get("error_code")
                    ):
                        code = obj.get("error_code") if isinstance(obj, dict) else "?"
                        msg = obj.get("error_msg") or obj.get("message") or ""
                        raise LLMError(f"豆包错误 {code}: {msg}（登录态失效或风控）")
                    if not isinstance(obj, dict):
                        continue
                    # CHUNK_DELTA 增量文本（首选）
                    if isinstance(obj.get("text"), str) and obj["text"]:
                        got_text = True
                        yield obj["text"]
                    # STREAM_CHUNK 全量块兜底
                    for patch in obj.get("patch_op") or []:
                        pv = (patch or {}).get("patch_value") or {}
                        for cb in pv.get("content_block") or []:
                            if (cb or {}).get("block_type") == 10000:
                                t = ((cb.get("content") or {}).get("text_block") or {}).get("text")
                                if t and not got_text:
                                    got_text = True
                                    yield t
                    if event == "SSE_REPLY_END":
                        break
        if not got_text:
            raise LLMError("豆包未返回文本（登录态可能失效）")
    except httpx.HTTPError as e:
        raise LLMError(f"豆包网络错误: {e}") from e
