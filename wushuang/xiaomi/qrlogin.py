"""小米账号扫码登录（米家/小米账号 APP 扫码）。

协议四步（社区多项目实测）：
1. serviceLogin?sid=<sid> 取 qs/_sign/callback 与初始 Cookie（deviceId 等，
   后续请求必须携带，否则扫码后回 70036）
2. longPolling/loginUrl 取二维码图片 URL(qr) 与长轮询地址(lp)
3. 长轮询 lp 等待扫码确认，成功返回 userId/passToken/ssecurity/nonce/location
4. location + clientSign 换 serviceToken（clientSign=base64(sha1("nonce=<n>&<ssecurity>"))）

参考：mijiaAPI、xiaomi-auth-sdk、MiForge/migate 等公开实现。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from urllib import parse

import aiohttp

_LOGGER = logging.getLogger(__name__)

ACCOUNT = "https://account.xiaomi.com"
START_PREFIX = b"&&&START&&&"


def _parse(raw: bytes) -> dict:
    if raw.startswith(START_PREFIX):
        raw = raw[len(START_PREFIX):]
    return json.loads(raw)


class XiaomiQrLogin:
    def __init__(self, session: aiohttp.ClientSession, sid: str = "micoapi"):
        self.session = session
        self.sid = sid
        self.lp: str = ""
        self.qr_url: str = ""
        self.login_url: str = ""

    async def start(self) -> dict:
        # 步骤1：取会话参数与初始 Cookie
        async with self.session.get(
            f"{ACCOUNT}/pass/serviceLogin?sid={self.sid}&_json=true",
            headers={"User-Agent": "Mozilla/5.0"},
        ) as r:
            resp = _parse(await r.read())
        qs = resp.get("qs", f"%3Fsid%3D{self.sid}%26_json%3Dtrue")
        callback = resp.get("callback", "https://api2.mina.mi.com/sts")
        # 步骤2：取二维码与长轮询地址
        params = {
            "sid": self.sid,
            "_qrsize": "240",
            "qs": qs,
            "callback": callback,
            "_hasLogo": "false",
            "serviceParam": resp.get("serviceParam", ""),
            "_locale": "zh_CN",
        }
        async with self.session.get(
            f"{ACCOUNT}/longPolling/loginUrl", params=params,
            headers={"User-Agent": "Mozilla/5.0"},
        ) as r:
            info = _parse(await r.read())
        if info.get("code") != 0:
            raise RuntimeError(f"获取二维码失败: {info}")
        self.qr_url = info.get("qr", "")
        self.lp = info.get("lp", "")
        self.login_url = info.get("loginUrl", "")
        return {"qr_url": self.qr_url, "login_url": self.login_url}

    async def poll_once(self, timeout: float = 30.0) -> dict | None:
        """单次长轮询；扫码确认返回 payload，未扫码/超时返回 None。"""
        if not self.lp:
            return None
        try:
            async with self.session.get(
                self.lp, timeout=aiohttp.ClientTimeout(total=timeout),
                headers={"User-Agent": "Mozilla/5.0"},
            ) as r:
                raw = await r.read()
        except (aiohttp.ClientError, TimeoutError):
            return None
        if not raw:
            return None
        try:
            data = _parse(raw)
        except (ValueError, json.JSONDecodeError):
            return None
        if data.get("code") == 0 and data.get("userId"):
            return data
        return None

    async def finish(self, payload: dict) -> dict:
        """步骤4：换 serviceToken，组装 MiService token。"""
        nonce = payload["nonce"]
        ssecurity = payload["ssecurity"]
        nsec = f"nonce={nonce}&{ssecurity}"
        client_sign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        location = payload["location"]
        sep = "&" if "?" in location else "?"
        url = f"{location}{sep}clientSign={parse.quote(client_sign)}"
        async with self.session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as r:
            await r.read()
            cookies = r.cookies
        service_token = cookies.get("serviceToken")
        if service_token is None:
            # 从会话 jar 兜底取
            jar_cookies = self.session.cookie_jar.filter_cookies(url)
            service_token = jar_cookies.get("serviceToken")
        if service_token is None:
            raise RuntimeError("换取 serviceToken 失败（401/cookie 缺失）")
        st_value = service_token.value if hasattr(service_token, "value") else str(service_token)
        # deviceId 取初始 Cookie
        device_id = ""
        for c in self.session.cookie_jar:
            if c.key == "deviceId":
                device_id = c.value
                break
        return {
            "deviceId": device_id or "qrlogin",
            "userId": str(payload["userId"]),
            "passToken": payload["passToken"],
            "loginMode": "qr",
            self.sid: (ssecurity, st_value),
        }
