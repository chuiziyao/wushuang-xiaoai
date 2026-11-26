"""汽水音乐桥接：通过 qishui-api sidecar（MIT）抖音扫码登录并同步歌单。

VIP 加密音频暂不支持下载（见 client 模块说明）。
"""

from .client import QishuiClient, QishuiError

__all__ = ["QishuiClient", "QishuiError"]
