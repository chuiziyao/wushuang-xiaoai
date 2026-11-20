"""汽水音乐客户端测试：MockTransport 模拟 qishui-api sidecar 契约。"""

import httpx
import pytest

from wushuang.music.qishui.client import QishuiClient, QishuiError


def _ok(data):
    return httpx.Response(200, json={"code": 0, "message": "success", "data": data})


async def test_qr_flow():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/qrcode":
            return _ok({"token": "tok1", "qrcode": "aGVsbG8=",
                        "qrcode_index_url": "https://sso.douyin.com/x"})
        if request.url.path == "/auth/qrcode/status":
            import json
            assert json.loads(request.content)["token"] == "tok1"
            calls["n"] += 1
            seq = {1: {"status": "1"}, 2: {"status": "2"},
                   3: {"status": "3", "auth": {"sessionid": "sid1"}},
                   4: {"status": "4"}}
            return _ok(seq[calls["n"]])
        return httpx.Response(404)

    c = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    start = await c.qr_start()
    assert start["key"] == "tok1"
    assert start["qrimg"] == "data:image/png;base64,aGVsbG8="
    assert (await c.qr_check("tok1"))["state"] == "waiting"
    assert (await c.qr_check("tok1"))["state"] == "scanned"
    done = await c.qr_check("tok1")
    assert done["state"] == "done" and done["cookie"] == "sessionid=sid1"
    assert (await c.qr_check("tok1"))["state"] == "expired"
    await c.close()


async def test_login_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers.get("Cookie") == "sessionid=sid1"
        return _ok({"profile": {"id": "777", "nickname": "汽水用户", "is_vip": False}})

    c = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    c.set_cookie("sessionid=sid1")
    info = await c.login_status()
    assert info == {"logged_in": True, "uid": "777", "nickname": "汽水用户"}
    await c.close()

    c2 = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    assert (await c2.login_status())["logged_in"] is False  # 无 cookie
    await c2.close()


async def test_me_playlists():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"playlists": [
            {"id": "7096700219496368135", "title": "我喜欢的音乐", "count_tracks": 12},
            {"id": "123", "title": "车载", "count_tracks": 30}], "total_num": 2})

    c = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    pls = await c.user_playlists("777")
    assert pls[0] == {"id": "7096700219496368135",
                      "name": "我喜欢的音乐", "trackCount": 12}
    await c.close()


async def test_playlist_tracks_cursor_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/playlist/detail"
        cursor = request.url.params.get("cursor", "")
        if not cursor:
            return _ok({"media_resources": [
                {"track": {"id": "t1", "name": "歌A",
                           "artists": [{"name": "歌手A"}], "album": {"name": "专辑A"}}},
                {"track": None},  # 非曲目资源应跳过
            ], "next_cursor": "c2"})
        assert cursor == "c2"
        return _ok({"media_resources": [
            {"track": {"id": "t2", "name": "歌B", "artists": [], "album": None}}],
            "next_cursor": ""})

    c = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    tracks = await c.playlist_tracks("7096700219496368135")
    assert tracks == [
        {"id": "t1", "name": "歌A", "artist": "歌手A", "album": "专辑A"},
        {"id": "t2", "name": "歌B", "artist": "", "album": ""}]
    await c.close()


async def test_song_urls_plain_and_encrypted():
    def handler(request: httpx.Request) -> httpx.Response:
        tid = request.url.params["track_id"]
        if tid == "t1":
            return _ok({"audio_url": "https://cdn.example/a.mp3?x=1",
                        "has_spade_a": False, "spade_a": ""})
        if tid == "t2":
            return _ok({"audio_url": "https://cdn.example/b.mflac",
                        "has_spade_a": True, "spade_a": "abc"})
        return _ok({"audio_url": "", "has_spade_a": False})

    c = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    urls = await c.song_urls(["t1", "t2", "t3"])
    assert urls["t1"] == {"url": "https://cdn.example/a.mp3?x=1", "type": "mp3", "br": 0}
    assert urls["t2"]["url"] is None  # VIP 加密曲
    assert urls["t3"]["url"] is None  # 无地址
    await c.close()


async def test_error_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 1, "message": "ERR_REQUEST_FORBIDDEN"})

    c = QishuiClient("http://qishui", transport=httpx.MockTransport(handler))
    with pytest.raises(QishuiError, match="FORBIDDEN"):
        await c.qr_start()
    await c.close()
