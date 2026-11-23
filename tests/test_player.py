"""播放控制能力测试：原生指令通道、Web 点歌队列、曲库分页。"""

import pytest

from wushuang.config import Settings
from wushuang.music.engine import MusicEngine
from wushuang.storage import Storage
from wushuang.xiaomi.speaker import Speaker


class FakeMina:
    def __init__(self):
        self.calls = []

    async def ubus_request(self, deviceId, method, path, message):
        self.calls.append((deviceId, method, path, message))
        return {"code": 0, "data": {"code": 0}}


async def test_speaker_nlp_channel():
    mina = FakeMina()
    sp = Speaker(mina, "d1", "测试", "LX06")
    assert await sp.nlp("播放今天的新闻") is True
    did, method, path, msg = mina.calls[0]
    assert (did, method, path) == ("d1", "ai_service", "mibrain")
    assert msg == {"tts": 1, "nlp": 1, "nlp_text": "播放今天的新闻"}
    assert await sp.nlp("") is True and len(mina.calls) == 1


class FakeSpeaker:
    did = "d1"
    name = "测试音箱"

    def __init__(self):
        self.urls = []

    async def play_url(self, url):
        self.urls.append(url)
        return True

    async def play_status(self):
        return {"status": 1, "volume": 20}

    async def tts(self, text):
        pass


@pytest.fixture
async def env(tmp_path):
    storage = Storage(tmp_path / "t.db")
    await storage.open()
    eng = MusicEngine(storage, Settings(data_dir=tmp_path, music_dir=tmp_path))
    yield storage, eng
    eng._stop_monitor("d1")
    await storage.close()


async def test_play_queue_single_and_playlist(env):
    storage, eng = env
    pid = await storage.add_playlist("歌单A", "local")
    t1 = await storage.upsert_track("歌A", "/m/a.mp3", artist="歌手A", playlist_id=pid)
    t2 = await storage.upsert_track("歌B", "/m/b.mp3", artist="歌手B", playlist_id=pid)
    sp = FakeSpeaker()
    ok = await eng.play_queue(sp, [await storage.get_track(t1)], "点歌：歌A")
    assert ok and len(sp.urls) == 1
    np = eng.now_playing("d1")
    assert np["title"] == "歌A" and np["total"] == 1 and np["paused"] is False
    ok = await eng.play_queue(sp, await storage.tracks_by_playlist(pid), "歌单A")
    assert ok and eng.now_playing("d1")["total"] == 2
    assert await eng.play_queue(sp, [], "空") is False


async def test_tracks_page_and_counts(env):
    storage, eng = env
    pid = await storage.add_playlist("歌单A", "local")
    for i in range(5):
        await storage.upsert_track(f"歌{i}", f"/m/{i}.mp3", playlist_id=pid)
    await storage.upsert_track("散曲", "/m/x.mp3")
    rows, total = await storage.tracks_page(pid, 1, 2)
    assert total == 5 and len(rows) == 2
    rows2, _ = await storage.tracks_page(pid, 3, 2)
    assert len(rows2) == 1
    rows3, total3 = await storage.tracks_page(None, 1, 100)
    assert total3 == 6
    counts = await storage.playlist_track_counts()
    assert counts == {pid: 5}
