"""播放引擎：每台音箱独立的播放队列、循环模式、自动切歌、语音控制。

播放方式与 xiaomusic 相同：把 NAS 的 HTTP 媒体地址推给音箱
（player_play_url），音箱自行拉流，全程不经过音箱端音乐会员。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

import mutagen

from ..config import Settings
from ..dispatcher import Intent
from ..storage import Storage
from ..utils.text import normalize_music_name
from ..xiaomi.speaker import Speaker

_LOGGER = logging.getLogger(__name__)


@dataclass
class PlayState:
    tracks: list[dict] = field(default_factory=list)
    index: int = 0
    mode: str = "order"  # order | loop_one | shuffle
    playlist_name: str = ""
    playing: bool = False  # 最近一次推流是否成功
    paused: bool = False  # 用户暂停（巡检不得切歌）
    paused_at: float = 0.0
    started_at: float = 0.0  # 当前曲目开始播放时间（monotonic；推流失败也记录，用于重试）
    idle_hits: int = 0  # 连续空闲判定次数（防抖，避免状态抖动切掉刚起的歌）
    fail_hits: int = 0  # 连续推流失败次数（超限判定登录态失效，停止无限重试）

    def current(self) -> dict | None:
        if not self.tracks or not (0 <= self.index < len(self.tracks)):
            return None
        return self.tracks[self.index]

    def advance(self, direction: int = 1) -> bool:
        if not self.tracks:
            return False
        if self.mode == "loop_one" and direction == 0:
            return True
        if self.mode == "shuffle":
            self.index = random.randrange(len(self.tracks))
        else:
            self.index = (self.index + direction) % len(self.tracks)
        return True


# 视为"非播放中"的状态码：0/2/3（机型差异：S12A 空闲=0 停止=2；部分机型 3=空闲）
_IDLE_STATES = (0, "0", 2, "2", 3, "3")
# 时长兜底余量（秒）：LX06 等机型 status 恒为 1，只能按曲目时长判断播完
_OVERDUE_MARGIN = 8.0
# 推流失败后的重试间隔（秒）
_RETRY_AFTER = 10.0
# 无时长信息时的估算曲目长度（秒）
_DEFAULT_DURATION = 240.0


def _advance_decision(st: PlayState, status: dict | None, now: float) -> str:
    """巡检决策（纯函数，便于测试）：返回 'advance' / 'retry' / 'wait'。

    空闲判定需连续两次（约 10s）防抖；时长超时兜底覆盖状态不可靠机型。
    """
    if not st.tracks or st.paused:
        st.idle_hits = 0
        return "wait"
    if not st.playing:
        # 上次推流失败：间隔重试，避免永久停摆
        if st.started_at and now - st.started_at >= _RETRY_AFTER:
            return "retry"
        return "wait"
    state = (status or {}).get("status")
    if state in _IDLE_STATES:
        st.idle_hits += 1
        if st.idle_hits >= 2:
            return "advance"
        return "wait"
    st.idle_hits = 0
    cur = st.current() or {}
    dur = float(cur.get("duration") or 0) or _DEFAULT_DURATION
    if st.started_at and now - st.started_at > dur + _OVERDUE_MARGIN:
        return "advance"
    return "wait"


class MusicEngine:
    def __init__(self, storage: Storage, settings: Settings):
        self.storage = storage
        self.settings = settings
        self._states: dict[str, PlayState] = {}
        self._monitors: dict[str, asyncio.Task] = {}

    # ---------- 对外状态（仪表盘用） ----------
    def now_playing(self, did: str) -> dict | None:
        st = self._states.get(did)
        if not st:
            return None
        cur = st.current()
        if not cur:
            return None
        return {
            "title": cur["title"],
            "artist": cur["artist"],
            "track_id": cur["id"],
            "playlist": st.playlist_name,
            "index": st.index + 1,
            "total": len(st.tracks),
            "mode": st.mode,
            "playing": st.playing,
            "paused": st.paused,
        }

    async def play_queue(self, speaker: Speaker, tracks: list[dict],
                         playlist_name: str, cfg: dict | None = None) -> bool:
        """Web/APP 点歌入口：把给定曲目列表作为队列立即播放。"""
        if not tracks:
            return False
        st = self._states.setdefault(speaker.did, PlayState())
        await self._start(speaker, st, cfg or {}, tracks, playlist_name)
        return st.playing

    # ---------- 指令入口 ----------
    async def handle(self, speaker: Speaker, intent: Intent, cfg: dict) -> None:
        did = speaker.did
        st = self._states.setdefault(did, PlayState())
        action = intent.action

        if action == "pause":
            await speaker.pause()
            st.playing = False
            st.paused = True
            st.paused_at = time.monotonic()
            return
        if action == "resume":
            await speaker.resume()
            st.playing = True
            if st.paused and st.paused_at:
                st.started_at += time.monotonic() - st.paused_at
            st.paused = False
            st.paused_at = 0.0
            return
        if action == "stop":
            await speaker.stop()
            st.playing = False
            st.paused = False
            self._stop_monitor(did)
            return
        if action == "next":
            if st.advance(1):
                await self._play_current(speaker, st, cfg)
            return
        if action == "prev":
            if st.advance(-1):
                await self._play_current(speaker, st, cfg)
            return
        if action in ("volume_up", "volume_down"):
            status = await speaker.play_status()
            vol = int((status or {}).get("volume", 30))
            await speaker.set_volume(vol + (10 if action == "volume_up" else -10))
            return
        if action == "volume_set":
            await speaker.set_volume(intent.value or 30)
            return
        if action in ("loop_one", "loop_all", "shuffle"):
            st.mode = {"loop_one": "loop_one", "loop_all": "order",
                       "shuffle": "shuffle"}[action]
            await speaker.tts(
                {"loop_one": "已切换单曲循环", "loop_all": "已切换顺序播放",
                 "shuffle": "已切换随机播放"}[action]
            )
            return

        if action == "play_playlist":
            await self._play_by_name(speaker, st, cfg, intent.keyword)
            return
        if action == "play_artist":
            tracks = await self.storage.search_tracks(intent.keyword, limit=100)
            tracks = [t for t in tracks if intent.keyword in (t["artist"] or "")]
            if tracks:
                await self._start(speaker, st, cfg, tracks,
                                  playlist_name=f"{intent.keyword} 的歌曲")
            else:
                await speaker.tts(f"没有找到{intent.keyword}的歌曲")
            return
        if action == "play_song":
            await self._play_by_name(speaker, st, cfg, intent.keyword)
            return

    # ---------- 内部 ----------
    async def _find_playlist(self, keyword: str) -> dict | None:
        """先精确匹配歌单名，再按归一化（中文数字↔阿拉伯数字）兜底，备注名同样可匹配。"""
        pl = await self.storage.get_playlist_by_name(keyword)
        if pl:
            return pl
        norm = normalize_music_name(keyword)
        if not norm:
            return None
        for p in await self.storage.list_playlists():
            if normalize_music_name(p["name"]) == norm:
                return p
            remark = p.get("remark") or ""
            if remark and normalize_music_name(remark) == norm:
                return p
        return None

    async def _play_by_name(
        self, speaker: Speaker, st: PlayState, cfg: dict, keyword: str
    ) -> None:
        # 1) 关键词恰好是歌单名（或数字写法变体）→ 播歌单
        pl = await self._find_playlist(keyword)
        if pl:
            tracks = await self.storage.tracks_by_playlist(pl["id"])
            if tracks:
                await self._start(speaker, st, cfg, tracks, playlist_name=pl["name"])
                return
        # 2) 模糊搜索曲库
        tracks = await self.storage.search_tracks(keyword, limit=50)
        if tracks:
            await self._start(speaker, st, cfg, tracks, playlist_name=f"搜索:{keyword}")
            return
        await speaker.tts(f"没有找到{keyword}，可以试试在后台同步网易云歌单")

    async def _start(
        self,
        speaker: Speaker,
        st: PlayState,
        cfg: dict,
        tracks: list[dict],
        playlist_name: str,
    ) -> None:
        st.tracks = tracks
        st.index = 0
        st.playlist_name = playlist_name
        st.paused = False
        st.paused_at = 0.0
        st.idle_hits = 0
        if st.mode == "shuffle":
            st.index = random.randrange(len(tracks))
        await self._play_current(speaker, st, cfg)
        self._start_monitor(speaker, st)

    async def _play_current(self, speaker: Speaker, st: PlayState, cfg: dict) -> None:
        cur = st.current()
        if not cur:
            return
        if (await self.storage.get_setting("music_confirm_tts", "false")) == "true":
            await speaker.tts(f"好的，播放{cur['title']}")
        url = f"{self.settings.speaker_base_url()}/media/{cur['id']}"
        ok = await speaker.play_url(url)
        st.playing = ok
        # 失败也记录时间：巡检据此间隔重试，避免永久停摆
        st.started_at = time.monotonic()
        if ok:
            st.fail_hits = 0
        else:
            st.fail_hits += 1
            _LOGGER.warning("[%s] 推流失败: %s", speaker.name, url)

    def _start_monitor(self, speaker: Speaker, st: PlayState) -> None:
        self._stop_monitor(speaker.did)
        self._monitors[speaker.did] = asyncio.create_task(
            self._monitor(speaker, st), name=f"music-monitor-{speaker.did}"
        )

    def _stop_monitor(self, did: str) -> None:
        task = self._monitors.pop(did, None)
        if task and not task.done():
            task.cancel()

    async def _ensure_duration(self, st: PlayState) -> None:
        """旧数据 duration=0 时懒读音频标签并回写，保证按真实时长切歌。"""
        cur = st.current()
        if not cur or float(cur.get("duration") or 0) > 0:
            return
        path = cur.get("path")
        if not path:
            return

        def _read() -> float:
            try:
                a = mutagen.File(path)
                return float(a.info.length) if a and a.info else 0.0
            except Exception:  # noqa: BLE001
                return 0.0

        dur = await asyncio.to_thread(_read)
        if dur > 0:
            cur["duration"] = dur
            await self.storage.db.execute(
                "UPDATE tracks SET duration=? WHERE id=?", (dur, cur["id"])
            )
            await self.storage.db.commit()

    async def _monitor(self, speaker: Speaker, st: PlayState) -> None:
        """播放巡检：播完自动下一首。

        双保险：ubus 空闲判定（连续两次防抖）+ 真实曲目时长超时兜底
        （LX06 等机型 status 恒为 1 不可靠）；切歌/重试前先 stop 复位设备
        会话，避免"拉流但不发声"；推流失败间隔重试；暂停时不切歌。
        """
        try:
            while True:
                try:
                    await asyncio.sleep(5)
                    await self._ensure_duration(st)
                    status = await speaker.play_status()
                    decision = _advance_decision(st, status, time.monotonic())
                    if decision == "wait":
                        continue
                    if decision == "retry":
                        if st.fail_hits >= 6:
                            _LOGGER.error(
                                "[%s] 连续推流失败，小米登录态可能失效；"
                                "停止重试，请到账号页重新米家扫码", speaker.name)
                            acc = await self.storage.get_xiaomi_account()
                            if acc:
                                await self.storage.update_xiaomi_token(
                                    acc.get("token_enc"), "expired")
                            self._stop_monitor(speaker.did)
                            return
                        _LOGGER.info("[%s] 推流重试 → %s", speaker.name,
                                     (st.current() or {}).get("title", ""))
                        await speaker.stop()
                        await self._play_current(speaker, st, {})
                        continue
                    if st.mode == "loop_one":
                        await speaker.stop()
                        await self._play_current(speaker, st, {})
                    elif st.advance(1):
                        st.idle_hits = 0
                        _LOGGER.info("[%s] 自动切歌 → %s", speaker.name,
                                     (st.current() or {}).get("title", ""))
                        await speaker.stop()
                        await self._play_current(speaker, st, {})
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - 单轮异常不致命，下轮继续
                    _LOGGER.warning("播放巡检异常，下轮继续", exc_info=True)
        except asyncio.CancelledError:
            raise
