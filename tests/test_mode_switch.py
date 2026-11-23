"""双模式测试：变身口令识别、原生技能放行、原生模式全放行。"""

from wushuang.dispatcher import (
    MODE_NATIVE,
    MODE_REPLIES,
    MODE_WUSHUANG,
    mode_command,
    route,
)


def test_mode_command_wake_and_direction():
    assert mode_command("小爱同学，变身", MODE_NATIVE) == MODE_WUSHUANG
    assert mode_command("变身", MODE_NATIVE) == MODE_WUSHUANG
    # 已在无双时"变身"仍进无双（重复播报确认），符合口令语义
    assert mode_command("小爱同学，变身", MODE_WUSHUANG) == MODE_WUSHUANG
    assert mode_command("小爱同学，变回去", MODE_WUSHUANG) == MODE_NATIVE
    assert mode_command("恢复原始", MODE_WUSHUANG) == MODE_NATIVE
    assert mode_command("退出无双", MODE_WUSHUANG) == MODE_NATIVE
    assert mode_command("进入无双", MODE_NATIVE) == MODE_WUSHUANG
    assert mode_command("无双模式", MODE_NATIVE) == MODE_WUSHUANG
    assert mode_command("请帮我变身", MODE_NATIVE) == MODE_WUSHUANG


def test_mode_command_no_false_positive():
    assert mode_command("播放变身奥特曼的歌曲", MODE_NATIVE) is None
    assert mode_command("播放歌单666", MODE_NATIVE) is None
    assert mode_command("今天天气怎么样", MODE_NATIVE) is None
    assert mode_command("", MODE_NATIVE) is None


def test_mode_replies_present():
    assert "无双小爱状态" in MODE_REPLIES[MODE_WUSHUANG]
    assert "大模型" in MODE_REPLIES[MODE_WUSHUANG]
    assert "原始" in MODE_REPLIES[MODE_NATIVE]


def test_native_skills_pass_through():
    """无双状态下原生技能不再被音乐路由拦截（新闻被当歌名的 bug）。"""
    for q in ["播放今天的新闻", "听新闻", "播放睡前故事", "讲个评书",
              "今天天气怎么样", "明天天气", "定一个明天8点的闹钟",
              "提醒我十分钟 later", "打开客厅的灯", "关闭空调"]:
        assert route(q).kind == "pass", q


def test_music_and_control_still_intercepted():
    assert route("播放晴天").kind == "music"
    assert route("播放歌单666").action == "play_playlist"
    assert route("暂停").action == "pause"
    assert route("请帮我解释量子力学").kind == "llm"
