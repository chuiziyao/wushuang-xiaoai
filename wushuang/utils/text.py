"""文本工具：TTS 分句、文件名清洗。"""

from __future__ import annotations

import re
from typing import Iterable, Iterator

_STRONG_PUNCT = "。！？!?；;\n"
_SOFT_PUNCT = "，,、：: "
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TTS_NOISE = re.compile(r"[*#`~]+")


def clean_for_tts(text: str) -> str:
    """去掉 Markdown 噪声符号，压缩空白，适合朗读。"""
    text = _TTS_NOISE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class SentenceBuffer:
    """有状态分句器：喂入流式增量，产出适合 TTS 的完整句子。"""

    def __init__(self, min_len: int = 6, max_len: int = 60):
        self.min_len = min_len
        self.max_len = max_len
        self.buf = ""

    def feed(self, chunk: str) -> list[str]:
        if not chunk:
            return []
        self.buf += chunk
        out: list[str] = []
        while self.buf:
            cut = -1
            for i, ch in enumerate(self.buf):
                if ch in _STRONG_PUNCT and i + 1 >= self.min_len:
                    cut = i + 1
                    break
            if cut == -1 and len(self.buf) >= self.max_len:
                soft = max(self.buf.rfind(p, 0, self.max_len) for p in _SOFT_PUNCT)
                cut = soft + 1 if soft + 1 >= self.min_len else self.max_len
            if cut == -1:
                break
            piece = clean_for_tts(self.buf[:cut])
            self.buf = self.buf[cut:]
            if piece:
                out.append(piece)
        return out

    def flush(self) -> str:
        tail = clean_for_tts(self.buf)
        self.buf = ""
        return tail


def split_sentences(
    chunks: Iterable[str], min_len: int = 6, max_len: int = 60
) -> Iterator[str]:
    """把 LLM 流式增量切成适合 TTS 播报的句子（生成器版）。

    - 优先在强标点（。！？；换行）处切分；
    - 片段不足 min_len 时继续累积，避免"好。"这类超短播报；
    - 超过 max_len 时在软标点（逗号等）处强制切分，找不到则硬切。
    """
    buf = SentenceBuffer(min_len, max_len)
    for chunk in chunks:
        yield from buf.feed(chunk)
    tail = buf.flush()
    if tail:
        yield tail


def sanitize_filename(name: str, max_len: int = 120) -> str:
    name = _BAD_FILENAME.sub("_", name).strip().strip(".")
    name = re.sub(r"\s+", " ", name)
    return name[:max_len] or "unnamed"
