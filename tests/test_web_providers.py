"""网页版 Cookie 提取与元宝客户端测试。"""

import httpx
import pytest

from wushuang.llm.client import LLMError
from wushuang.llm.yuanbao import (
    _flatten_messages,
    parse_cookie,
    stream_chat_yuanbao,
)
from wushuang.web.api import _extract_web_key, _merge_extra


def test_extract_doubao_sessionid():
    raw = "uuid=abc; sessionid=XYZ123; other=1"
    assert _extract_web_key("doubao-web", raw) == "XYZ123"
    assert _extract_web_key("doubao-web", "XYZ123") == "XYZ123"


def test_extract_doubao_sessionid_ss():
    raw = "ttwid=1; sessionid_ss=SS_TOKEN9; uid_tt_ss=2"
    assert _extract_web_key("doubao-web", raw) == "SS_TOKEN9"


def test_extract_qwen_ticket():
    raw = "cna=1; tongyi_sso_ticket=TICKET9; x=2"
    assert _extract_web_key("qwen-web", raw) == "TICKET9"


def test_yuanbao_keeps_full_cookie():
    raw = "hy_user=U1; hy_token=T1"
    assert _extract_web_key("yuanbao-web", raw) == raw


def test_merge_extra():
    s = _merge_extra('{"uskey":"old"}', None, "agentX")
    import json
    d = json.loads(s)
    assert d["uskey"] == "old" and d["agent_id"] == "agentX"
    d2 = json.loads(_merge_extra(s, "new", None))
    assert d2["uskey"] == "new" and d2["agent_id"] == "agentX"


def test_parse_cookie_requires_hy():
    with pytest.raises(LLMError):
        parse_cookie("foo=bar")
    assert parse_cookie("hy_user=1; hy_token=2") == "hy_user=1; hy_token=2"


def test_flatten_messages():
    msgs = [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好呀"},
        {"role": "user", "content": "讲个笑话"},
    ]
    text = _flatten_messages(msgs)
    assert "[系统指令]" in text and "讲个笑话" in text and "助手: 你好呀" in text


async def test_yuanbao_stream_mock():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/conversation/create"):
            assert request.headers.get("x-uskey") == "USKEY"
            return httpx.Response(200, json={"id": "conv1"})
        if "/api/chat/" in request.url.path:
            body = (
                'data: {"type":"think","msg":"思考中"}\n'
                'data: {"type":"text","msg":"你好"}\n'
                'data: {"type":"text","msg":"，世界"}\n'
                'data: [DONE]\n'
            )
            return httpx.Response(
                200, content=body.encode(),
                headers={"content-type": "text/event-stream"},
            )
        if request.url.path.endswith("/clear"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404)

    out = []
    async for delta in stream_chat_yuanbao(
        cookie="hy_user=1; hy_token=2", uskey="USKEY", model="hunyuan",
        messages=[{"role": "user", "content": "hi"}],
        transport=httpx.MockTransport(handler),
    ):
        out.append(delta)
    assert "".join(out) == "你好，世界"
    assert any("/conversation/create" in c for c in calls)
    assert any(c.endswith("/clear") for c in calls)  # 会话已清理


async def test_yuanbao_bad_cookie():
    def handler(request):
        return httpx.Response(200, json={"code": 401, "msg": "unauthorized"})

    with pytest.raises(LLMError):
        async for _ in stream_chat_yuanbao(
            cookie="hy_user=1; hy_token=2", uskey="", model="hunyuan",
            messages=[{"role": "user", "content": "hi"}],
            transport=httpx.MockTransport(handler),
        ):
            pass
