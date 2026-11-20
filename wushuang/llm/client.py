"""OpenAI 兼容聊天客户端（流式优先，自动兼容非流式响应）。"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator

import httpx

_LOGGER = logging.getLogger(__name__)


class LLMError(Exception):
    pass


async def stream_chat(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    timeout: float = 60.0,
    temperature: float = 0.7,
) -> AsyncIterator[str]:
    """POST {base_url}/chat/completions，逐段产出回答文本。"""
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "temperature": temperature,
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0)
        ) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    raise LLMError(f"HTTP {resp.status_code}: {body[:300]}")
                ctype = resp.headers.get("content-type", "")
                if "text/event-stream" not in ctype:
                    # 某些中转不支持流式：整包返回
                    body = (await resp.aread()).decode("utf-8", errors="replace")
                    try:
                        obj = json.loads(body)
                        content = obj["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, json.JSONDecodeError) as e:
                        raise LLMError(f"无法解析响应: {body[:200]}") from e
                    if content:
                        yield content
                    return
                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        yield content
    except httpx.HTTPError as e:
        raise LLMError(f"网络错误: {e}") from e
