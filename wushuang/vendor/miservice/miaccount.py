import base64
import hashlib
import json
import logging
import os
import random
import string
from urllib import parse
from aiohttp import ClientSession

_LOGGER = logging.getLogger(__package__)


class MiVerificationRequired(Exception):
    """[wushuang patch] 小米要求安全验证（新设备/异地登录）。

    携带 notificationUrl，用户在浏览器完成验证后，用同一 deviceId 重试登录。
    """

    def __init__(self, url: str, security_status=None):
        self.url = url
        self.security_status = security_status
        super().__init__(f"Xiaomi security verification required: {url[:80]}...")

# [wushuang patch] 用静态 UA 列表替换 fake_useragent（离线可用，无需联网更新数据集）
_UA_LIST = [
    "Mozilla/5.0 (iPhone; CPU iPhone OS 14_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (iPad; CPU OS 14_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
]


class _StaticUserAgent:
    @property
    def random(self):
        return random.choice(_UA_LIST)


def get_random(length):
    return "".join(random.sample(string.ascii_letters + string.digits, length))


class MiTokenStore:
    def __init__(self, token_path):
        self.token_path = token_path

    def load_token(self):
        if os.path.isfile(self.token_path):
            try:
                with open(self.token_path) as f:
                    return json.load(f)
            except Exception:
                _LOGGER.exception("Exception on load token from %s", self.token_path)
        return None

    def save_token(self, token=None):
        if token:
            try:
                with open(self.token_path, "w") as f:
                    json.dump(token, f, indent=2)
            except Exception:
                _LOGGER.exception("Exception on save token to %s", self.token_path)
        elif os.path.isfile(self.token_path):
            os.remove(self.token_path)


class MiAccount:
    def __init__(self, session: ClientSession, username, password, token_store=None):
        self.session = session
        self.username = username
        self.password = password
        self.token_store = (
            MiTokenStore(token_store) if isinstance(token_store, str) else token_store
        )
        self.token = token_store is not None and self.token_store.load_token()
        self.ua = _StaticUserAgent()  # [wushuang patch] 静态 UA
        self.now_ua = self.ua.random

    async def login(self, sid):
        if not self.token:
            self.token = {"deviceId": get_random(16).upper()}
        try:
            # [wushuang patch] 扫码登录的 token：只尝试 passToken 静默刷新，
            # 失败绝不走密码验证（会触发安全验证并覆盖扫码成果）。
            if self.token.get("loginMode") == "qr":
                resp = await self._serviceLogin(f"serviceLogin?sid={sid}&_json=true")
                if resp.get("code") == 0 and "userId" in resp:
                    return await self._finish_login(resp, sid)
                _LOGGER.warning("扫码 token 刷新失败，保留现有 token（需重新扫码时由界面提示）")
                return False
            # [wushuang patch] 用户完成安全验证后，用同一会话上下文重试 auth2，
            # 而不是发起新 serviceLogin（新上下文会使已完成的验证作废）。
            pending = self.token.get("pendingAuth")
            if pending and pending.get("sid") == sid:
                data = dict(pending["data"])
                data["user"] = self.username
                data["hash"] = hashlib.md5(self.password.encode()).hexdigest().upper()
                resp = await self._serviceLogin("serviceLoginAuth2", data)
                if resp.get("code") == 0 and "userId" in resp:
                    self.token.pop("pendingAuth", None)
                    self.token.pop("verificationUrl", None)
                    return await self._finish_login(resp, sid)
                url = resp.get("notificationUrl") or resp.get("captchaUrl") or ""
                if url:
                    # [wushuang patch] 保持原始上下文不变（不更新 qs/_sign），
                    # 确保用户手中的验证链接与重试上下文始终一致。
                    self.token["verificationUrl"] = url
                    self.token["securityStatus"] = resp.get("securityStatus")
                    if self.token_store:
                        self.token_store.save_token(self.token)
                    raise MiVerificationRequired(url, resp.get("securityStatus"))
                # 上下文失效等其他错误：清除后走全新流程
                self.token.pop("pendingAuth", None)

            resp = await self._serviceLogin(f"serviceLogin?sid={sid}&_json=true")
            if resp["code"] != 0:
                # [wushuang patch] 扫码登录无密码：passToken 刷新失败时不得走
                # 密码验证（空密码必然失败并破坏已有会话），保留 token 返回失败。
                if not self.password:
                    _LOGGER.warning("passToken 刷新失败且无密码（扫码登录），保留现有 token")
                    return False
                data = {
                    "_json": "true",
                    "qs": resp["qs"],
                    "sid": resp["sid"],
                    "_sign": resp["_sign"],
                    "callback": resp["callback"],
                    "user": self.username,
                    "hash": hashlib.md5(self.password.encode()).hexdigest().upper(),
                }
                # [wushuang patch] 保存待验证会话上下文（不含密码）
                self.token["pendingAuth"] = {
                    "sid": sid,
                    "data": {k: data[k] for k in ("_json", "qs", "sid", "_sign", "callback")},
                }
                resp = await self._serviceLogin("serviceLoginAuth2", data)
                if resp["code"] != 0:
                    raise Exception(resp)

            # [wushuang patch] 密码正确但触发安全验证：无 userId，有 notificationUrl
            if "userId" not in resp:
                url = resp.get("notificationUrl") or resp.get("captchaUrl") or ""
                if url:
                    self.token["verificationUrl"] = url
                    self.token["securityStatus"] = resp.get("securityStatus")
                    raise MiVerificationRequired(url, resp.get("securityStatus"))
                raise Exception(resp)

            self.token.pop("pendingAuth", None)
            self.token.pop("verificationUrl", None)
            return await self._finish_login(resp, sid)

        except MiVerificationRequired:
            # [wushuang patch] 保留 deviceId/上下文/验证链接，供用户验证后重试
            if self.token_store:
                self.token_store.save_token(self.token)
            raise
        except Exception as e:
            # [wushuang patch] 扫码/无密码登录失败不清空 token
            if self.password and self.token.get("loginMode") != "qr":
                self.token = None
                if self.token_store:
                    self.token_store.save_token()
            _LOGGER.exception("Exception on login %s: %s", self.username, e)
            return False

    async def _finish_login(self, resp, sid):
        """[wushuang patch] 登录成功收尾：passToken + serviceToken。"""
        self.token["userId"] = resp["userId"]
        # passToken 刷新场景响应可能不带 passToken，保留原有值
        if resp.get("passToken"):
            self.token["passToken"] = resp["passToken"]
        serviceToken = await self._securityTokenService(
            resp["location"], resp["nonce"], resp["ssecurity"]
        )
        self.token[sid] = (resp["ssecurity"], serviceToken)
        if self.token_store:
            self.token_store.save_token(self.token)
        return True

    async def _serviceLogin(self, uri, data=None):
        self.now_ua = self.ua.random
        headers = {"User-Agent": self.now_ua}
        cookies = {"sdkVersion": "3.9", "deviceId": self.token["deviceId"]}
        if "passToken" in self.token:
            cookies["userId"] = self.token["userId"]
            cookies["passToken"] = self.token["passToken"]
        else:
            cookies["passToken"] = ""
        url = "https://account.xiaomi.com/pass/" + uri
        async with self.session.request(
            "GET" if data is None else "POST",
            url,
            data=data,
            cookies=cookies,
            headers=headers,
            ssl=False,
        ) as r:
            raw = await r.read()
        resp = json.loads(raw[11:])
        _LOGGER.debug("%s: %s", uri, resp)
        return resp

    async def _securityTokenService(self, location, nonce, ssecurity):
        nsec = "nonce=" + str(nonce) + "&" + ssecurity
        clientSign = base64.b64encode(hashlib.sha1(nsec.encode()).digest()).decode()
        async with self.session.get(
            location + "&clientSign=" + parse.quote(clientSign)
        ) as r:
            serviceToken = r.cookies["serviceToken"].value
            if not serviceToken:
                raise Exception(await r.text())
        return serviceToken

    async def mi_request(self, sid, url, data, headers, relogin=True):
        headers["User-Agent"] = self.now_ua
        if (self.token and sid in self.token) or await self.login(sid):  # Ensure login
            cookies = {
                "userId": self.token["userId"],
                "serviceToken": self.token[sid][1],
            }
            content = data(self.token, cookies) if callable(data) else data
            method = "GET" if data is None else "POST"
            _LOGGER.info("%s %s", url, content)
            async with self.session.request(
                method, url, data=content, cookies=cookies, headers=headers
            ) as r:
                status = r.status
                if status == 200:
                    resp = await r.json(content_type=None)
                    code = resp["code"]
                    if code == 0:
                        return resp
                    if "auth" in resp.get("message", "").lower():
                        status = 401
                else:
                    resp = await r.text()
            if status == 401 and relogin:
                # [wushuang patch] 扫码 token 过期时不清空走密码流程，提示重新扫码
                if self.token and self.token.get("loginMode") == "qr":
                    raise Exception(f"QR session expired (401), re-scan required: {url}")
                _LOGGER.warn("Auth error on request %s %s, relogin...", url, resp)
                self.token = None  # Auth error, reset login
                return await self.mi_request(sid, url, data, headers, False)
        else:
            resp = "Login failed"
        raise Exception(f"Error {url}: {resp}")
