"""腾讯元宝网页版内置客户端（实验性）。

协议参考 chenwr727/yuanbao-free-api（MIT）：元宝网页聊天为纯 HTTP API，
但需要浏览器请求中的 x-uskey 签名头。本实现让用户从 F12 粘贴整段 Cookie
与 x-uskey，无需在 NAS 上跑浏览器。

限制（如实告知）：
- x-uskey 可能随会话/时间失效，失效后需重新从浏览器抓取；
- 元宝接口变动可能导致失效；仅限个人自用，违反平台条款风险自负。
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

from .client import LLMError

_LOGGER = logging.getLogger(__name__)

BASE = "https://yuanbao.tencent.com"
CREATE_URL = f"{BASE}/api/user/agent/conversation/create"
CLEAR_URL = f"{BASE}/api/user/agent/conversation/v1/clear"
CHAT_URL = f"{BASE}/api/chat/{{chat_id}}"
DEFAULT_AGENT_ID = "naQivTmsDa"

# 与上游项目一致的模型映射
MODEL_MAPPING = {
    "hunyuan": {"model": "hunyuan_gpt_175B_0404", "functions": None},
    "hunyuan-t1": {"model": "hunyuan_t1", "functions": None},
    "hunyuan-search": {"model": "hunyuan_gpt_175B_0404",
                       "functions": ["supportInternetSearch"]},
    "hunyuan-t1-search": {"model": "hunyuan_t1",
                          "functions": ["supportInternetSearch"]},
    "deepseek-v3": {"model": "deep_seek_v3", "functions": None},
    "deepseek-r1": {"model": "deep_seek", "functions": None},
    "deepseek-v3-search": {"model": "deep_seek_v3",
                           "functions": ["supportInternetSearch"]},
    "deepseek-r1-search": {"model": "deep_seek",
                           "functions": ["supportInternetSearch"]},
}


def parse_cookie(cookie: str) -> str:
    """从整段 Cookie 中提取元宝所需部分（hy_user/hy_token 等）。"""
    if "hy_user=" not in cookie and "hy_token=" not in cookie:
        raise LLMError("Cookie 中未找到 hy_user/hy_token，请确认已登录元宝网页版")
    return cookie.strip()


def _flatten_messages(messages: list[dict]) -> str:
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


def _headers(cookie: str, uskey: str, agent_id: str) -> dict:
    h = {
        "Cookie": cookie,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "Origin": BASE,
        "Referer": f"{BASE}/chat/{agent_id}",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if uskey:
        h["x-uskey"] = uskey
    return h


async def stream_chat_yuanbao(
    cookie: str,
    uskey: str,
    model: str,
    messages: list[dict],
    timeout: float = 90.0,
    agent_id: str = DEFAULT_AGENT_ID,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[str]:
    """创建会话 → 流式聊天 → 产出文本增量 → 清理会话。"""
    info = MODEL_MAPPING.get((model or "hunyuan").lower())
    if not info:
        raise LLMError(f"元宝不支持的模型: {model}（可选: {', '.join(MODEL_MAPPING)}）")
    headers = _headers(parse_cookie(cookie), uskey, agent_id)
    prompt = _flatten_messages(messages)
    chat_id = ""
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0), transport=transport
        ) as client:
            r = await client.post(CREATE_URL, json={"agentId": agent_id},
                                  headers=headers)
            if r.status_code != 200:
                raise LLMError(f"元宝创建会话 HTTP {r.status_code}: {r.text[:200]}")
            try:
                chat_id = r.json().get("id", "")
            except ValueError:
                chat_id = ""
            if not chat_id:
                raise LLMError(f"元宝创建会话失败（Cookie/x-uskey 可能已失效）: {r.text[:200]}")

            body = {
                "model": "gpt_175B_0404",
                "prompt": prompt,
                "plugin": "Adaptive",
                "displayPrompt": prompt,
                "displayPromptType": 1,
                "options": {"imageIntention": {
                    "needIntentionModel": True, "backendUpdateFlag": 2,
                    "intentionStatus": True}},
                "multimedia": [],
                "agentId": agent_id,
                "supportHint": 1,
                "version": "v2",
                "chatModelId": info["model"],
            }
            if info["functions"]:
                body["supportFunctions"] = info["functions"]

            got_text = False
            async with client.stream("POST", CHAT_URL.format(chat_id=chat_id),
                                     json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    detail = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"元宝聊天 HTTP {resp.status_code}: {detail[:200]}")
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    if not data.startswith("{"):
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("type") == "text" and chunk.get("msg"):
                        got_text = True
                        yield chunk["msg"]
            if not got_text:
                raise LLMError("元宝未返回文本（可能触发风控或接口已变更）")
    except httpx.HTTPError as e:
        raise LLMError(f"元宝网络错误: {e}") from e
    finally:
        if chat_id:
            try:
                async with httpx.AsyncClient(timeout=15, transport=transport) as c2:
                    await c2.post(CLEAR_URL, json={
                        "conversationIds": [chat_id],
                        "uiOptions": {"noToast": True},
                    }, headers=headers)
            except Exception:  # noqa: BLE001 - 清理失败不影响主流程
                _LOGGER.debug("元宝会话清理失败", exc_info=True)
