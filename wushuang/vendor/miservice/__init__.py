"""内置（vendor）的小米云服务协议库。

来源: https://github.com/yihong0618/MiService （MIT, Yonsm / yihong0618）
内置版本: 上游 main 分支 2026-11 快照（miservice_fork 2.9.3 同源）。
本地修改: miaccount.py 中 fake_useragent 替换为静态 UA 列表（离线可用），
其余逻辑与上游一致。仅保留产品所需模块，CLI 相关模块未内置。
"""

from .miaccount import MiAccount, MiTokenStore, MiVerificationRequired
from .minaservice import MiNAService
from .miioservice import MiIOService

__all__ = ["MiAccount", "MiTokenStore", "MiVerificationRequired", "MiNAService", "MiIOService"]
