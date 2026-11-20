"""调度路由测试：音乐/控制/LLM/放行 四类意图。"""

from wushuang.dispatcher import Intent, route


def test_play_song():
    i = route("播放歌曲晴天")
    assert i.kind == "music" and i.action == "play_song" and i.keyword == "晴天"


def test_play_song_polite_prefix():
    i = route("请播放歌曲晴天")
    assert i.kind == "music" and i.keyword == "晴天"
    i2 = route("帮我播放一首后来")
    assert i2.kind == "music" and i2.keyword == "后来"


def test_play_artist():
    i = route("播放周杰伦的歌")
    assert i.kind == "music" and i.action == "play_artist" and i.keyword == "周杰伦"
    i2 = route("我想听林俊杰的歌曲")
    assert i2.action == "play_artist" and i2.keyword == "林俊杰"


def test_play_playlist():
    i = route("播放歌单我喜欢的音乐")
    assert i.kind == "music" and i.action == "play_playlist"
    assert i.keyword == "我喜欢的音乐"


def test_listen_variants():
    for q in ["我想听后来", "来一首晴天", "放一首歌后来", "听一首后来"]:
        assert route(q).kind == "music", q


def test_controls():
    cases = {
        "暂停播放": "pause", "暂停": "pause", "别唱了": "pause",
        "停止播放": "stop", "闭嘴": "stop",
        "继续播放": "resume", "下一首": "next", "切歌": "next",
        "上一首": "prev", "音量大一点": "volume_up", "小声点": "volume_down",
        "单曲循环": "loop_one", "顺序播放": "loop_all", "随机播放": "shuffle",
    }
    for q, action in cases.items():
        i = route(q)
        assert i.kind == "control" and i.action == action, q


def test_volume_set():
    i = route("音量百分之三十")
    assert i.kind == "control" and i.action == "volume_set" and i.value == 30
    i2 = route("音量调到30")
    assert i2.action == "volume_set" and i2.value == 30
    assert route("音量百分之五十").value == 50
    assert route("音量调到一百").value == 100
    assert route("音量百分之十").value == 10


def test_llm_triggers():
    for q in ["请帮我解释量子力学", "为什么天空是蓝色的", "什么是相对论",
              "帮我写一首诗", "聊聊三体"]:
        assert route(q).kind == "llm", q


def test_llm_wake_words():
    i = route("无双，今天适合洗车吗")
    assert i.kind == "llm" and i.keyword == "今天适合洗车吗"
    i2 = route("豆包，讲个笑话")
    assert i2.kind == "llm" and i2.keyword == "讲个笑话"


def test_music_beats_llm_prefix():
    # "帮我播放..." 必须走音乐而不是 LLM
    i = route("帮我播放歌曲晴天")
    assert i.kind == "music"


def test_passthrough():
    for q in ["定一个明天早上8点的闹钟", "今天天气怎么样", "打开客厅的灯", ""]:
        assert route(q).kind == "pass", q


def test_custom_triggers():
    i = route("你好呀", ["你好"])
    assert i.kind == "llm"
    i2 = route("请解释一下", [])
    assert i2.kind == "pass"


def test_explicit_singer_and_search():
    i = route("播放歌手周杰伦")
    assert i.kind == "music" and i.action == "play_artist" and i.keyword == "周杰伦"
    i2 = route("搜索歌曲晴天")
    assert i2.kind == "music" and i2.action == "play_song" and i2.keyword == "晴天"


def test_stop_synonyms():
    for q in ["关闭音乐", "关掉音乐", "退出播放"]:
        assert route(q).action == "stop", q


def test_trailing_punct_stripped():
    assert route("下一首！").action == "next"
    assert route("播放歌曲晴天。").keyword == "晴天"
