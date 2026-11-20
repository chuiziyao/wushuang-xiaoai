"""网易云桥接：通过 ncm-api sidecar 登录并同步歌单到本地。

同步引擎与调度器已提升为多平台通用模块（wushuang.music.sync /
wushuang.music.scheduler），本包只保留网易云协议客户端与平台适配。
"""

from .client import NcmClient, NcmError
from .platform import NeteasePlatform

__all__ = ["NcmClient", "NcmError", "NeteasePlatform"]
