"""统一调度器：每台音箱一个轮询循环，一句话只走一条分支。

路由优先级：播放控制 > 音乐点播 > LLM 触发词 > 放行给小爱原生处理。
这是合并 mi-gpt 与 xiaomusic 的核心收益——两个独立容器各自轮询会互相
抢话，统一调度后天然互斥，且闹钟/米家控制等原生能力完全不受影响。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

from .storage import Storage
from .xiaomi.account import XiaomiAccountManager
from .xiaomi.speaker import Speaker

_LOGGER = logging.getLogger(__name__)

DEFAULT_LLM_TRIGGERS = [
    "请", "帮我", "请问", "为什么", "什么是", "怎么", "如何",
    "解释", "聊聊", "你觉得", "告诉我", "无双", "豆包", "千问",
]
_WAKE_WORDS = ["无双", "豆包", "千问"]
_LEADING_POLITE = ["请帮我", "帮我", "请给我", "给我", "麻烦你", "麻烦", "请", "我想", "我要"]

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_num(s: str) -> int | None:
    """解析阿拉伯或中文数字（0-100，音量场景）。"""
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            cur = _CN_DIGITS[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur

# (正则, action) —— 播放控制类
_CONTROL_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:暂停|暂停播放|别唱了|别放了)$"), "pause"),
    (re.compile(r"^(?:停止|停止播放|关闭音乐|关掉音乐|退出播放|闭嘴)$"), "stop"),
    (re.compile(r"^(?:继续|继续播放|恢复播放|接着放)$"), "resume"),
    (re.compile(r"^(?:下一首|下一曲|切歌|换一首|换个歌)$"), "next"),
    (re.compile(r"^(?:上一首|上一曲|前一首)$"), "prev"),
    (re.compile(r"^(?:音量(?:大|高)(?:一点|点|些)?|大声(?:一点|点|些)?|声音大一点)$"), "volume_up"),
    (re.compile(r"^(?:音量(?:小|低)(?:一点|点|些)?|小声(?:一点|点|些)?|声音小一点)$"), "volume_down"),
    (re.compile(r"^音量(?:调到|设置为|设为|百分之)?([\d零一二两三四五六七八九十百]+)$"), "volume_set"),
    (re.compile(r"^(?:单曲循环|单曲重复)$"), "loop_one"),
    (re.compile(r"^(?:顺序播放|列表循环|循环播放)$"), "loop_all"),
    (re.compile(r"^(?:随机播放|随机放)$"), "shuffle"),
]

# (正则, action) —— 音乐点播类，捕获组为关键词
_MUSIC_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^(?:播放|放|来|听)(?:歌单|播放列表|列表)(.+)$"), "play_playlist"),
    (re.compile(r"^(?:播放歌手|搜索歌手)(.+)$"), "play_artist"),
    (re.compile(r"^(?:搜索歌曲|在线播放)(.+)$"), "play_song"),
    (re.compile(r"^(?:播放|放|来|听|我想听|我要听)(?:一首|一首歌|歌曲|歌|音乐)?(.+)$"), "play_song"),
]


@dataclass
class Intent:
    kind: str  # music | control | llm | pass
    action: str = ""
    keyword: str = ""
    value: int | None = None


def _strip_leading(text: str, prefixes: list[str]) -> str:
    for p in sorted(prefixes, key=len, reverse=True):
        if text.startswith(p) and len(text) > len(p):
            return text[len(p):].lstrip("，,。 ".strip())
    return text


def route(query: str, llm_triggers: list[str] | None = None) -> Intent:
    """纯函数路由，便于单元测试。"""
    q = (query or "").strip().rstrip("。.!！?？~～ ")
    if not q:
        return Intent("pass")

    # 1) 播放控制
    for pattern, action in _CONTROL_RULES:
        m = pattern.match(q)
        if m:
            value = _cn_num(m.group(1)) if m.groups() else None
            return Intent("control", action=action, value=value)

    # 2) 音乐点播（先剥掉礼貌前缀，"请播放歌曲X"也是点歌）
    m_q = _strip_leading(q, _LEADING_POLITE)
    for pattern, action in _MUSIC_RULES:
        m = pattern.match(m_q)
        if m:
            kw = m.group(1).strip().rstrip("。.!！?？")
            if not kw:
                break
            if action == "play_song" and re.search(r"的歌(?:曲)?$", kw):
                return Intent("music", action="play_artist",
                              keyword=re.sub(r"的歌(?:曲)?$", "", kw))
            return Intent("music", action=action, keyword=kw)

    # 3) LLM 触发
    triggers = llm_triggers if llm_triggers is not None else DEFAULT_LLM_TRIGGERS
    for wake in _WAKE_WORDS:
        if q.startswith(wake):
            rest = q[len(wake):].lstrip("，,。：: ")
            return Intent("llm", keyword=rest or q)
    for t in triggers:
        if q.startswith(t):
            return Intent("llm", keyword=q)

    # 4) 放行：小爱原生处理（闹钟、天气、米家控制等）
    return Intent("pass")


class Dispatcher:
    """管理所有已启用音箱的轮询任务。"""

    def __init__(
        self,
        manager: XiaomiAccountManager,
        storage: Storage,
        music_engine,
        llm_engine,
    ):
        self.manager = manager
        self.storage = storage
        self.music = music_engine
        self.llm = llm_engine
        self._tasks: dict[str, asyncio.Task] = {}
        self._devices: dict[str, dict] = {}

    async def sync(self) -> None:
        """按数据库中的启用状态增停轮询任务（Web 后台改配置后调用）。"""
        devices = await self.storage.list_devices()
        want: dict[str, dict] = {}
        if self.manager.ready:
            for d in devices:
                if d["enabled"]:
                    want[d["did"]] = d
        for did in list(self._tasks):
            if did not in want:
                self._tasks[did].cancel()
                del self._tasks[did]
                self._devices.pop(did, None)
        for did, dev in want.items():
            self._devices[did] = dev
            if did not in self._tasks or self._tasks[did].done():
                self._tasks[did] = asyncio.create_task(
                    self._loop(dev), name=f"dispatch-{did}"
                )

    async def stop_all(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        self._tasks.clear()
        self._devices.clear()

    def running_devices(self) -> list[str]:
        return [did for did, t in self._tasks.items() if not t.done()]

    async def _loop(self, dev: dict) -> None:
        did = dev["did"]
        speaker = Speaker(self.manager.mina, did, dev["name"], dev["hardware"],
                          manager=self.manager)
        cfg = json.loads(dev.get("config_json") or "{}")
        triggers = cfg.get("llm_triggers") or DEFAULT_LLM_TRIGGERS
        _LOGGER.info("开始轮询音箱 %s (%s, %s)", dev["name"], did, dev["hardware"])

        baseline_ms: int | None = None
        seen: set[str] = set()
        try:
            while True:
                asks = await speaker.latest_asks()
                if baseline_ms is None:
                    # 首轮只建基线，不处理历史消息（避免时钟偏差与重启回放）
                    baseline_ms = max(
                        [a["timestamp_ms"] for a in asks] + [int(time.time() * 1000)]
                    )
                    seen.update(a["request_id"] for a in asks)
                else:
                    for a in sorted(asks, key=lambda x: x["timestamp_ms"]):
                        rid = a["request_id"]
                        if a["timestamp_ms"] <= baseline_ms or rid in seen:
                            continue
                        seen.add(rid)
                        if len(seen) > 500:
                            seen = set(sorted(seen)[-200:])
                        await self._handle(speaker, cfg, triggers, a["question"])
                interval = float(
                    await self.storage.get_setting("poll_interval", "1.0")
                )
                await asyncio.sleep(max(0.3, interval))
        except asyncio.CancelledError:
            _LOGGER.info("停止轮询音箱 %s", dev["name"])
            raise
        except Exception:  # noqa: BLE001 - 单设备崩溃不影响其他设备
            _LOGGER.exception("音箱 %s 轮询异常退出", dev["name"])

    async def _handle(
        self, speaker: Speaker, cfg: dict, triggers: list[str], question: str
    ) -> None:
        intent = route(question, triggers)
        if intent.kind == "pass":
            return
        _LOGGER.info(
            "[%s] %r -> %s/%s kw=%r",
            speaker.name, question, intent.kind, intent.action, intent.keyword,
        )
        try:
            if intent.kind == "llm":
                await self.llm.ask(speaker, intent.keyword, cfg)
            else:
                await self.music.handle(speaker, intent, cfg)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("处理指令失败: %r", question)
