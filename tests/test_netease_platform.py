"""网易云平台适配测试：扫码状态归一化、歌单/曲目结构归一化。"""

import json

import httpx
import pytest

from wushuang.music.netease.client import NcmClient
from wushuang.music.netease.platform import NeteasePlatform


def _transport(handler):
    return httpx.MockTransport(handler)


async def test_qr_state_mapping():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/qr/key":
            return httpx.Response(200, json={"data": {"unikey": "k1"}})
        if request.url.path == "/login/qr/create":
            return httpx.Response(200, json={"data": {"qrurl": "u", "qrimg": "img"}})
        if request.url.path == "/login/qr/check":
            code = int(request.url.params.get("code", "801"))
            body = {"code": code, "message": "m"}
            if code == 803:
                body["cookie"] = "MUSIC_U=abc; Path=/"
            return httpx.Response(200, json=body)
        return httpx.Response(404)

    # qr_check 的 code 由远端决定，这里用不同 unikey 触发不同 code
    codes = {"k800": 800, "k801": 801, "k802": 802, "k803": 803}

    def handler2(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/qr/check":
            key = request.url.params.get("key", "")
            code = codes.get(key, 801)
            body = {"code": code, "message": "m"}
            if code == 803:
                body["cookie"] = "MUSIC_U=abc"
            return httpx.Response(200, json=body)
        return handler(request)

    ncm = NcmClient("http://ncm", transport=_transport(handler2))
    plat = NeteasePlatform(ncm)
    start = await plat.qr_start()
    assert start["key"] == "k1" and start["qrimg"] == "img"
    assert (await plat.qr_check("k800"))["state"] == "expired"
    assert (await plat.qr_check("k801"))["state"] == "waiting"
    assert (await plat.qr_check("k802"))["state"] == "scanned"
    done = await plat.qr_check("k803")
    assert done["state"] == "done" and done["cookie"] == "MUSIC_U=abc"
    await ncm.close()


async def test_playlist_tracks_normalization():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/playlist/track/all":
            return httpx.Response(200, json={"songs": [
                {"id": 186016, "name": "晴天",
                 "ar": [{"name": "周杰伦"}], "al": {"name": "叶惠美"}},
            ]})
        if request.url.path == "/user/playlist":
            return httpx.Response(200, json={"playlist": [
                {"id": 123, "name": "我喜欢的音乐", "trackCount": 1},
            ]})
        return httpx.Response(404)

    ncm = NcmClient("http://ncm", transport=_transport(handler))
    plat = NeteasePlatform(ncm)
    tracks = await plat.playlist_tracks("123")
    assert tracks == [{"id": "186016", "name": "晴天",
                       "artist": "周杰伦", "album": "叶惠美"}]
    pls = await plat.user_playlists("42")
    assert pls == [{"id": "123", "name": "我喜欢的音乐", "trackCount": 1}]
    await ncm.close()
