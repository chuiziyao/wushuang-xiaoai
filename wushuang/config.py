"""运行配置：环境变量引导 + 数据库运行时设置。

环境变量只负责"启动引导"（目录、端口、初始密码、sidecar 地址），
业务设置（轮询间隔、触发词、TTS 语速等）存 SQLite，可在 Web 后台修改。
"""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


def guess_lan_ip() -> str:
    """猜测本机局域网 IP（音箱必须能访问到本服务的 HTTP）。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


@dataclass
class Settings:
    data_dir: Path
    music_dir: Path
    host: str = "0.0.0.0"
    port: int = 8080
    public_url: str = ""  # 例如 http://192.168.1.10:8080，留空则自动猜测
    admin_password: str = "wushuang"  # 首次启动的初始管理密码
    ncm_api_url: str = "http://127.0.0.1:3000"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wushuang.db"

    @property
    def key_path(self) -> Path:
        return self.data_dir / "secret.key"

    def speaker_base_url(self) -> str:
        """音箱拉取音乐流所用的基地址。"""
        if self.public_url:
            return self.public_url.rstrip("/")
        return f"http://{guess_lan_ip()}:{self.port}"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("WUSHUANG_DATA_DIR", "./data")).resolve()
        music_dir = Path(os.environ.get("WUSHUANG_MUSIC_DIR", "./music")).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        music_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data_dir,
            music_dir=music_dir,
            host=os.environ.get("WUSHUANG_HOST", "0.0.0.0"),
            port=int(os.environ.get("WUSHUANG_PORT", "8080")),
            public_url=os.environ.get("WUSHUANG_PUBLIC_URL", ""),
            admin_password=os.environ.get("WUSHUANG_ADMIN_PASSWORD", "wushuang"),
            ncm_api_url=os.environ.get(
                "WUSHUANG_NCM_API_URL", "http://127.0.0.1:3000"
            ),
        )


# 数据库运行时设置的默认值（Web 后台可改）
DEFAULT_RUNTIME_SETTINGS: dict[str, str] = {
    "poll_interval": "1.0",  # 对话轮询间隔（秒）
    "tts_chars_per_second": "4.5",  # 小爱 TTS 语速估计（字/秒），用于分句播报间隔
    "tts_min_chunk": "6",  # 分句播报最小字数
    "tts_max_chunk": "60",  # 超长句强制切分字数
    "llm_system_prompt": (
        "你是安装在小米智能音箱里的中文语音助手「无双小爱同学」。"
        "回答要口语化、简洁、适合朗读，一般不超过 200 字；"
        "不要使用 Markdown、列表符号或表情；数字尽量用中文读法。"
    ),
    "llm_history_turns": "6",  # 携带的历史对话轮数
    "llm_timeout": "60",  # 单次 LLM 请求超时（秒）
    "llm_interrupt_playing": "true",  # 回答前是否暂停当前播放
    "music_confirm_tts": "false",  # 播放前是否先说"好的"
    "netease_download_level": "exhigh",  # 网易云下载音质: standard/higher/exhigh/lossless/hires
    "netease_sync_concurrency": "3",  # 同步下载并发数
}
