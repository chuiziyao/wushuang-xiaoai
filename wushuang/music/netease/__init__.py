"""网易云桥接：通过 ncm-api sidecar 登录并同步歌单到本地。"""

from .client import NcmClient
from .sync import NeteaseSync
from .scheduler import SyncScheduler

__all__ = ["NcmClient", "NeteaseSync", "SyncScheduler"]
