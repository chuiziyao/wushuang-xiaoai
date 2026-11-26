"""从本机 Edge/Chrome 浏览器 cookie 库自动提取指定网站 cookie。

用途：提取豆包 sessionid / 千问 tongyi_sso_ticket / 元宝 hy_token 等，
免去用户在 DevTools 里翻找。仅读取本机浏览器数据，不外传。

用法：
  python scripts/extract_browser_cookie.py doubao.com sessionid
  python scripts/extract_browser_cookie.py --list doubao.com
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def dpapi_decrypt(encrypted: bytes) -> bytes:
    blob_in = DATA_BLOB(len(encrypted), ctypes.create_string_buffer(encrypted, len(encrypted)))
    blob_out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    )
    if not ok:
        raise OSError("CryptUnprotectData 失败")
    data = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return data


def get_browser_key(user_data_dir: Path) -> bytes:
    local_state = json.loads((user_data_dir / "Local State").read_text(encoding="utf-8"))
    enc_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
    if enc_key[:5] != b"DPAPI":
        raise OSError("非 DPAPI 密钥格式")
    return dpapi_decrypt(enc_key[5:])


def decrypt_value(enc: bytes, key: bytes) -> str:
    if enc[:3] in (b"v10", b"v11"):
        nonce, ct = enc[3:15], enc[15:]
        return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8", errors="replace")
    if enc[:3] == b"v20":
        raise OSError("v20 应用绑定加密（需浏览器内部接口，改用扩展或设置页查看）")
    # 旧版 DPAPI 直接加密
    return dpapi_decrypt(enc).decode("utf-8", errors="replace")


def find_profiles(browser: str) -> list[Path]:
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    if browser == "edge":
        base = local / "Microsoft" / "Edge" / "User Data"
    else:
        base = local / "Google" / "Chrome" / "User Data"
    if not base.is_dir():
        return []
    profiles = []
    for name in ("Default", "Profile 1", "Profile 2", "Profile 3"):
        ud = base / name
        if (ud / "Network" / "Cookies").is_file() or (ud / "Cookies").is_file():
            profiles.append(ud)
    return profiles


def cookie_db_path(profile: Path) -> Path:
    p = profile / "Network" / "Cookies"
    return p if p.is_file() else profile / "Cookies"


def extract(browser: str, domain: str, name: str | None, list_all: bool) -> None:
    profiles = find_profiles(browser)
    if not profiles:
        print(f"未找到 {browser} 用户数据目录")
        return
    base = profiles[0].parent
    try:
        key = get_browser_key(base)
    except Exception as e:
        print(f"密钥解密失败: {e}")
        return
    found = False
    for profile in profiles:
        db = cookie_db_path(profile)
        try:
            # immutable 模式绕过浏览器文件锁直读
            conn = sqlite3.connect(f"{db.as_uri()}?immutable=1", uri=True)
            rows = conn.execute(
                "SELECT host_key, name, encrypted_value, path FROM cookies "
                "WHERE host_key LIKE ?", (f"%{domain}%",)
            ).fetchall()
            conn.close()
        except sqlite3.Error as e:
            print(f"[{profile.name}] 读取失败: {e}")
            continue
        for host, cname, enc, path in rows:
            if name and cname != name and not list_all:
                continue
            try:
                value = decrypt_value(enc, key)
            except Exception as e:
                print(f"[{profile.name}] {host} {cname}: 解密失败 ({e})")
                continue
            found = True
            if list_all:
                print(f"[{profile.name}] {host} {cname} = {value[:60]}{'...' if len(value) > 60 else ''}")
            else:
                print(f"PROFILE={profile.name} HOST={host} NAME={cname}")
                print(f"VALUE={value}")
    if not found:
        print(f"未找到 {domain} 的 cookie{'（名称 ' + name + '）' if name else ''}；请确认浏览器已登录该网站")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("domain")
    ap.add_argument("name", nargs="?", default=None)
    ap.add_argument("--browser", default="edge", choices=["edge", "chrome"])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    extract(args.browser, args.domain, args.name, args.list)
