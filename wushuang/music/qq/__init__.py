"""QQ音乐桥接：通过 QQMusicApi Web sidecar（GPLv3，独立容器隔离）登录并同步歌单。"""

from .client import QQClient, QQError

__all__ = ["QQClient", "QQError"]
