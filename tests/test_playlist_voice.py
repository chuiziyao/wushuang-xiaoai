"""歌单语音点播测试：路由 + 歌单名归一化兜底（666/六六六、四万首/4万首）。"""

import pytest

from wushuang.config import Settings
from wushuang.dispatcher import route
from wushuang.music.engine import MusicEngine, PlayState
from wushuang.storage import Storage
from wushuang.utils.text import normalize_music_name


def test_normalize_music_name():
    assert normalize_music_name("六六六") == "666"
    assert normalize_music_name("666") == "666"
    assert normalize_music_name("四万首") == "4万首"
    assert normalize_music_name("4万首") == "4万首"
    assert normalize_music_name("My Music") == "mymusic"
    assert normalize_music_name(" 平生 不晚 ") == "平生不晚"
    assert normalize_music_name("") == ""


def test_route_playlist_variants():
    i = route("播放歌单666")
    assert i.kind == "music" and i.action == "play_playlist" and i.keyword == "666"
    i2 = route("播放歌单六六六")
    assert i2.action == "play_playlist" and i2.keyword == "六六六"
    # 不带"歌单"二字也能点歌单（play_song 路径先查歌单名）
    i3 = route("播放4万首")
    assert i3.kind == "music" and i3.keyword == "4万首"
    i4 = route("请播放歌单平生不晚")
    assert i4.action == "play_playlist" and i4.keyword == "平生不晚"


class FakeSpeaker:
    did = "d1"
    name = "测试音箱"

    def __init__(self):
        self.urls: list[str] = []
        self.tts_msgs: list[str] = []

    async def play_url(self, url: str) -> bool:
        self.urls.append(url)
        return True

    async def tts(self, text: str) -> None:
        self.tts_msgs.append(text)

    async def play_status(self):
        return None


@pytest.fixture
async def engine(tmp_path):
    storage = Storage(tmp_path / "test.db")
    await storage.open()
    settings = Settings(data_dir=tmp_path, music_dir=tmp_path)
    eng = MusicEngine(storage, settings)
    yield eng, storage
    eng._stop_monitor(FakeSpeaker.did)
    await storage.close()


async def _seed(storage):
    for name in ("666", "四万首", "平生不晚"):
        pid = await storage.add_playlist(name, "netease", "1", f"/m/{name}")
        await storage.upsert_track(f"{name}的歌", f"/m/{name}/a.mp3",
                                   playlist_id=pid)


async def test_voice_matches_digit_variants(engine):
    eng, storage = engine
    await _seed(storage)
    sp = FakeSpeaker()

    # ASR 把 666 听成"六六六"
    st = PlayState()
    await eng._play_by_name(sp, st, {}, "六六六")
    assert st.playlist_name == "666" and len(sp.urls) == 1

    # ASR 把"四万首"听成"4万首"
    st2 = PlayState()
    await eng._play_by_name(sp, st2, {}, "4万首")
    assert st2.playlist_name == "四万首" and len(sp.urls) == 2

    # 精确名仍然优先
    st3 = PlayState()
    await eng._play_by_name(sp, st3, {}, "平生不晚")
    assert st3.playlist_name == "平生不晚" and len(sp.urls) == 3
    assert not sp.tts_msgs


async def test_voice_unknown_keyword_tts(engine):
    eng, storage = engine
    await _seed(storage)
    sp = FakeSpeaker()
    st = PlayState()
    await eng._play_by_name(sp, st, {}, "不存在的歌单")
    assert not sp.urls
    assert sp.tts_msgs and "没有找到" in sp.tts_msgs[0]


async def test_voice_matches_remark(engine):
    """备注名也可作为语音口令匹配（数字歌单名+人类可读备注）。"""
    eng, storage = engine
    pid = await storage.add_playlist("888", "netease", "9", remark="祁厅长DJ合集")
    await storage.upsert_track("歌", "/m/r.mp3", playlist_id=pid)
    sp = FakeSpeaker()
    st = PlayState()
    await eng._play_by_name(sp, st, {}, "祁厅长dj合集")
    assert st.playlist_name == "888" and len(sp.urls) == 1
