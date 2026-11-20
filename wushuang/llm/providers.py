"""LLM 服务商预设：网页版免费逆向 与 官方 API 双轨。

所有预设都走 OpenAI 兼容接口（/chat/completions），因此引擎无需关心
背后是免费网页中转还是官方付费 API——切换只是换 base_url/key/model。

网页版预设的 base_url 默认指向 compose 网络内的 free-api 服务名；
若单独部署 free-api，改成 http://127.0.0.1:8000/v1 之类即可。
"""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "doubao-web": {
        "label": "豆包网页版（免费·内置免签名）",
        "base_url": "builtin://doubao",
        "model": "doubao",
        "key_hint": "豆包网页版 Cookie 中的 sessionid_ss（或 sessionid）值；用无双Cookie助手扩展一键提取",
        "free": True,
        "note": "内置2026新协议客户端，无需中转容器；登录态过期需重新提取；仅限自用",
    },
    "qwen-web": {
        "label": "千问网页版（免费·逆向）",
        "base_url": "http://qwen-free-api:8000/v1",
        "model": "qwen3-235b-a22b",
        "key_hint": "通义网页版 Cookie 中的 tongyi_sso_ticket",
        "free": True,
        "note": "需自行部署 Qwen-Free-API 维护分支；注意甄别供应链安全，仅限自用",
    },
    "yuanbao-web": {
        "label": "腾讯元宝网页版（免费·内置实验性）",
        "base_url": "builtin://yuanbao",
        "model": "hunyuan",
        "key_hint": "元宝网页版整段 Cookie（含 hy_user/hy_token，F12→Network→任一请求→Cookie）",
        "free": True,
        "note": "内置实现，无需额外容器；还需 x-uskey 头（F12→Network→/api 请求→Request Headers→x-uskey），会失效需更新；模型可选 hunyuan/hunyuan-t1/deepseek-v3/deepseek-r1 及 -search 变体",
    },
    "doubao-ark": {
        "label": "豆包官方·火山方舟",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-1-6-250615",
        "key_hint": "火山方舟 API Key（模型名也可填推理接入点 ep-xxx）",
        "free": False,
        "note": "按量计费，新用户有免费额度；最稳定",
    },
    "qwen-dashscope": {
        "label": "千问官方·阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_hint": "阿里云百炼 API Key（sk-xxx）",
        "free": False,
        "note": "按量计费，新用户有免费额度；最稳定",
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_hint": "DeepSeek 开放平台 API Key",
        "free": False,
        "note": "按量计费，价格极低",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "key_hint": "任意 OpenAI 兼容服务的 API Key",
        "free": False,
        "note": "OneAPI / Ollama / 其他中转均可",
    },
}


def preset_defaults(preset: str) -> dict:
    return PRESETS.get(preset, PRESETS["custom"])
