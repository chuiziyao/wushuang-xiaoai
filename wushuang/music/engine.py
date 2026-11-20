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

from ..config import Settings
from ..dispatcher import Intent
from ..storage import Storage
from ..xiaomi.speaker import Speaker

_LOGGER = logging.getLogger(__name__)


@dataclass
class PlayState:
    tracks: list[dict] = field(default_factory=list)
    index: int = 0
    mode: str = "order"  # order | loop_one | shuffle
    playlist_name: str = ""
    playing: bool = False
    started_at: float = 0.0  # 当前曲目开始播放时间（monotonic）

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
        }

    # ---------- 指令入口 ----------
    async def handle(self, speaker: Speaker, intent: Intent, cfg: dict) -> None:
        did = speaker.did
        st = self._states.setdefault(did, PlayState())
        action = intent.action

        if action == "pause":
            await speaker.pause()
            st.playing = False
            return
        if action == "resume":
            await speaker.resume()
            st.playing = True
            return
        if action == "stop":
            await speaker.stop()
            st.playing = False
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
    async def _play_by_name(
        self, speaker: Speaker, st: PlayState, cfg: dict, keyword: str
    ) -> None:
        # 1) 关键词恰好是歌单名 → 播歌单
        pl = await self.storage.get_playlist_by_name(keyword)
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
        if ok:
            st.started_at = time.monotonic()
        if not ok:
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

    async def _monitor(self, speaker: Speaker, st: PlayState) -> None:
        """播放巡检：播完自动下一首。

        优先用 ubus 播放状态（1=播放中 2=暂停 3=空闲，机型有差异）；
        状态查询不可用时，按曲目时长 + 余量兜底切歌。
        """
        try:
            while True:
                await asyncio.sleep(5)
                if not st.playing or not st.tracks:
                    continue
                status = await speaker.play_status()
                advance = False
                if status is not None:
                    state = status.get("status")
                    advance = state in (3, "3", 0, "0")
                else:
                    cur = st.current()
                    dur = float((cur or {}).get("duration") or 0)
                    if dur > 0 and st.started_at > 0:
                        advance = (time.monotonic() - st.started_at) > dur + 5
                if advance:
                    if st.mode == "loop_one":
                        await self._play_current(speaker, st, {})
                    elif st.advance(1):
                        await self._play_current(speaker, st, {})
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _LOGGER.debug("播放巡检异常", exc_info=True)
