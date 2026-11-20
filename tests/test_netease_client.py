"""网易云客户端测试：用 httpx MockTransport 模拟 ncm-api 响应。"""

import json

import httpx
import pytest

from wushuang.music.netease.client import NcmClient, NcmError


def make_client(handler):
    return NcmClient("http://ncm-api:3000", transport=httpx.MockTransport(handler))


async def test_song_urls_parse():
    def handler(request):
        assert request.url.path == "/song/url/v1"
        assert request.url.params["id"] == "1,2"
        assert request.url.params["level"] == "lossless"
        return httpx.Response(200, json={"code": 200, "data": [
            {"id": 1, "url": "http://cdn/1.mp3", "type": "mp3", "br": 320000},
            {"id": 2, "url": None, "type": None, "br": 0},
        ]})

    client = make_client(handler)
    urls = await client.song_urls(["1", "2"], "lossless")
    assert urls["1"]["url"] == "http://cdn/1.mp3"
    assert urls["2"]["url"] is None
    assert urls["2"]["type"] == "mp3"  # 缺省回退
    await client.close()


async def test_song_urls_fallback_to_old_endpoint():
    """v1(xeapi) 失败时自动回退 /song/url(eapi)。"""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/song/url/v1":
            return httpx.Response(500, json={"code": 500, "msg": "xeapi key missing"})
        assert request.url.params["br"] == "999000"
        return httpx.Response(200, json={"code": 200, "data": [
            {"id": 7, "url": "http://cdn/7.flac", "type": "flac", "br": 999000},
        ]})

    client = make_client(handler)
    urls = await client.song_urls(["7"], "lossless")
    assert calls == ["/song/url/v1", "/song/url"]
    assert urls["7"]["url"] == "http://cdn/7.flac"
    assert urls["7"]["type"] == "flac"
    await client.close()


async def test_playlist_tracks_pagination():
    calls = {"n": 0}

    def handler(request):
        offset = int(request.url.params["offset"])
        calls["n"] += 1
        if offset == 0:
            songs = [{"id": i, "name": f"歌{i}"} for i in range(500)]
        else:
            songs = [{"id": 500, "name": "歌500"}]
        return httpx.Response(200, json={"code": 200, "songs": songs,
                                         "privileges": []})

    client = make_client(handler)
    songs = await client.playlist_tracks("999")
    assert len(songs) == 501 and calls["n"] == 2
    await client.close()


async def test_qr_check_codes():
    def handler(request):
        return httpx.Response(200, json={"code": 803, "cookie": "MUSIC_U=abc",
                                         "message": "授权成功"})

    client = make_client(handler)
    r = await client.qr_check("unikey")
    assert r["code"] == 803 and "MUSIC_U" in r["cookie"]
    await client.close()


async def test_cookie_header_sent():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={"code": 200, "data": {
            "code": 200, "account": {"id": 42}, "profile": {"nickname": "测试用户"}}})

    client = make_client(handler)
    client.set_cookie("MUSIC_U=abc")
    info = await client.login_status()
    assert seen["cookie"] == "MUSIC_U=abc"
    assert info["logged_in"] and info["uid"] == "42" and info["nickname"] == "测试用户"
    await client.close()


async def test_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("connection refused")

    client = make_client(handler)
    with pytest.raises(NcmError):
        await client.login_status()
    await client.close()
