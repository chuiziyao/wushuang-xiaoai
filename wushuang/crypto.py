"""敏感信息加密：Fernet 对称加密，密钥文件存于数据目录。

注意：密钥与数据库同目录，防的是"数据库文件被单独拷走"，
不防整机被入侵。NAS 本身的安全（强密码、不暴露公网）仍是第一道防线。
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretBox:
    def __init__(self, key_path: Path):
        self.key_path = key_path
        if key_path.is_file():
            key = key_path.read_bytes().strip()
        else:
            key = Fernet.generate_key()
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key_path.write_bytes(key)
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                pass  # Windows/部分文件系统不支持，忽略
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str | None) -> str:
        if not plaintext:
            return ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str | None) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""
