"""酷狗音乐桥接：通过 KuGouMusicApi sidecar（MIT）登录并同步歌单。"""

from .client import KugouClient, KugouError

__all__ = ["KugouClient", "KugouError"]
