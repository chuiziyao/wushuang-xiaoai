"""LLM 引擎：双轨服务商（网页版逆向 / 官方 API）+ 对话编排。"""

from .engine import LLMEngine
from .providers import PRESETS

__all__ = ["LLMEngine", "PRESETS"]
