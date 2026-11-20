"""文本工具测试：TTS 分句与文件名清洗。"""

from wushuang.utils.text import (
    SentenceBuffer,
    clean_for_tts,
    sanitize_filename,
    split_sentences,
)


def test_split_basic():
    chunks = ["你好，", "我是无双。", "今天天气不错！"]
    out = list(split_sentences(chunks, min_len=4))
    assert out == ["你好，我是无双。", "今天天气不错！"]


def test_split_min_len_accumulates():
    out = list(split_sentences(["好。", "好的。", "明白了。"], min_len=6))
    # 短句累积到足够长再播报
    assert all(len(s) >= 6 or s == out[-1] for s in out)
    assert "".join(out).replace(" ", "") == "好。好的。明白了。"


def test_split_max_len_forces_cut():
    long = "这是一段没有任何标点的超长回答" * 5
    out = list(split_sentences([long], min_len=6, max_len=30))
    assert len(out) >= 2
    assert all(len(s) <= 30 for s in out)


def test_buffer_stateful():
    buf = SentenceBuffer(min_len=4, max_len=50)
    assert buf.feed("你好") == []
    out = buf.feed("，世界。")
    assert out == ["你好，世界。"]
    assert buf.feed("尾巴没有标点") == []
    assert buf.flush() == "尾巴没有标点"


def test_clean_for_tts():
    assert clean_for_tts("**重点**：`代码` #标题") == "重点：代码 标题"


def test_sanitize_filename():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert sanitize_filename("  ") == "unnamed"
    assert len(sanitize_filename("长" * 300)) == 120
