"""播放巡检决策测试：LX06 状态恒 1 的时长兜底、S12A 状态切歌、失败重试、暂停不切。"""

import pytest

from wushuang.music.engine import (
    _DEFAULT_DURATION,
    _OVERDUE_MARGIN,
    _RETRY_AFTER,
    PlayState,
    _advance_decision,
)


def _st(duration=100.0, started=1000.0, playing=True, paused=False):
    st = PlayState(tracks=[{"id": 1, "title": "A", "duration": duration},
                           {"id": 2, "title": "B", "duration": duration}])
    st.playing = playing
    st.paused = paused
    st.started_at = started
    return st


def test_lx06_status_always_one_uses_duration_fallback():
    """LX06 实测 status 恒为 1：只能靠时长超时切歌（原 bug 根因）。"""
    st = _st(duration=100, started=1000)
    assert _advance_decision(st, {"status": 1}, now=1050) == "wait"
    assert _advance_decision(st, {"status": 1},
                             now=1000 + 100 + _OVERDUE_MARGIN + 1) == "advance"


def test_s12a_idle_and_stopped_states_advance():
    """空闲判定需连续两次（防抖），避免状态抖动切掉刚起的歌。"""
    st = _st()
    for state in (0, "0", 2, "2", 3, "3"):
        st.idle_hits = 0
        assert _advance_decision(st, {"status": state}, now=1001) == "wait"
        assert _advance_decision(st, {"status": state}, now=1006) == "advance"


def test_idle_debounce_resets_on_playing():
    st = _st()
    assert _advance_decision(st, {"status": 0}, now=1001) == "wait"
    assert _advance_decision(st, {"status": 1}, now=1006) == "wait"
    assert st.idle_hits == 0
    assert _advance_decision(st, {"status": 0}, now=1011) == "wait"
    assert _advance_decision(st, {"status": 0}, now=1016) == "advance"


def test_playing_state_waits():
    st = _st()
    assert _advance_decision(st, {"status": 1}, now=1001) == "wait"


def test_no_status_uses_duration():
    st = _st(duration=50, started=1000)
    assert _advance_decision(st, None, now=1040) == "wait"
    assert _advance_decision(st, None, now=1060) == "advance"


def test_missing_duration_uses_default_estimate():
    st = _st(duration=0, started=1000)
    assert _advance_decision(st, {"status": 1},
                             now=1000 + _DEFAULT_DURATION + _OVERDUE_MARGIN + 1) == "advance"


def test_failed_push_retries_after_interval():
    st = _st(playing=False, started=1000)
    assert _advance_decision(st, {"status": 1}, now=1005) == "wait"
    assert _advance_decision(st, {"status": 1},
                             now=1000 + _RETRY_AFTER) == "retry"


def test_paused_never_advances():
    st = _st(paused=True)
    assert _advance_decision(st, {"status": 0}, now=99999) == "wait"
    st2 = _st(playing=False, paused=True)
    assert _advance_decision(st2, None, now=99999) == "wait"


def test_empty_queue_waits():
    st = PlayState()
    assert _advance_decision(st, {"status": 0}, now=1) == "wait"


def test_pause_resume_compensates_elapsed(monkeypatch):
    """暂停期间不计入播放时长：resume 后 started_at 顺延。"""
    import wushuang.music.engine as eng

    clock = {"t": 1000.0}
    monkeypatch.setattr(eng.time, "monotonic", lambda: clock["t"])
    st = _st(duration=100, started=1000)
    st.paused = True
    st.paused_at = 1010.0
    clock["t"] = 1060.0  # 暂停了 50s
    # 模拟 resume 的补偿逻辑
    st.started_at += clock["t"] - st.paused_at
    st.paused = False
    assert st.started_at == 1050.0
    assert _advance_decision(st, {"status": 1}, now=1100) == "wait"
    assert _advance_decision(st, {"status": 1}, now=1159) == "advance"


async def test_monitor_gives_up_and_marks_expired(monkeypatch, tmp_path):
    """连续推流失败（如小米 401）超限后停止重试并标记账号 expired。"""
    import asyncio
    import itertools
    import types

    import wushuang.music.engine as eng
    from wushuang.config import Settings
    from wushuang.storage import Storage

    storage = Storage(tmp_path / "t.db")
    await storage.open()
    await storage.save_xiaomi_account("13800000000", "enc-pass")
    await storage.update_xiaomi_token("enc-token", "ok")
    music = eng.MusicEngine(storage, Settings(data_dir=tmp_path, music_dir=tmp_path))

    class DeadSpeaker:
        did = "d1"
        name = "测试"

        async def play_url(self, url):
            return False

        async def play_status(self):
            return None

        async def stop(self):
            return True

    st = eng.PlayState(tracks=[{"id": 1, "title": "A", "duration": 10}])
    st.playing = False
    st.started_at = 1000.0
    clock = itertools.count(1000, 30)
    monkeypatch.setattr(eng, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))

    async def fast_sleep(_):
        return None

    monkeypatch.setattr(eng.asyncio, "sleep", fast_sleep)
    sp = DeadSpeaker()
    try:
        await asyncio.wait_for(music._monitor(sp, st), timeout=10)
        assert st.fail_hits >= 6
        acc = await storage.get_xiaomi_account()
        assert acc["status"] == "expired"
        assert acc["token_enc"] == "enc-token"  # token 不被清空
    finally:
        await storage.close()
