"""QQ音乐客户端测试：MockTransport 模拟 QQMusicApi Web sidecar 契约。"""

import json

import httpx
import pytest

from wushuang.music.qq.client import QQClient, QQError


def _ok(data):
    return httpx.Response(200, json={"code": 0, "msg": "ok", "data": data})


async def test_qr_flow_qq():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p == "/login/qrcode/qq":
            return _ok({"identifier": "id1", "img": "data:image/png;base64,x",
                        "qr_type": "qq", "mimetype": "image/png", "data": "x"})
        if p == "/login/qrcode/qq/status":
            assert request.url.params["identifier"] == "id1"
            calls["n"] += 1
            seq = {1: {"event": 1}, 2: {"event": 2},
                   3: {"event": 0, "credential": {"musicid": 123, "musickey": "mk",
                                                  "openid": "oid"}},
                   4: {"event": 3}}
            return _ok(seq[calls["n"]])
        return httpx.Response(404)

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    start = await c.qr_start()
    assert start["key"] == "id1" and start["qrimg"].startswith("data:")
    assert (await c.qr_check("id1"))["state"] == "waiting"
    assert (await c.qr_check("id1"))["state"] == "scanned"
    done = await c.qr_check("id1")
    assert done["state"] == "done"
    assert done["cookie"] == "musicid=123; musickey=mk; openid=oid"
    assert (await c.qr_check("id1"))["state"] == "expired"
    await c.close()


async def test_qr_wx_type_and_refuse():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/qrcode/wx":
            return _ok({"identifier": "wx1", "img": "data:image/png;base64,y"})
        if request.url.path == "/login/qrcode/wx/status":
            return _ok({"event": 4})
        return httpx.Response(404)

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    await c.qr_start(login_type="wx")
    r = await c.qr_check("wx1")
    assert r["state"] == "expired" and "拒绝" in r["message"]
    await c.close()


async def test_login_status_and_nickname():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Cookie") == "musicid=123; musickey=mk"
        if request.url.path == "/login/check_expired":
            return _ok(False)
        if request.url.path == "/user/123/created_songlists":
            return _ok({"total": 1, "playlists": [
                {"id": 9, "dirid": 1, "title": "我喜欢", "songnum": 5, "nick": "QQ昵称"}]})
        return httpx.Response(404)

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    c.set_cookie("musicid=123; musickey=mk")
    info = await c.login_status()
    assert info == {"logged_in": True, "uid": "123", "nickname": "QQ昵称"}
    await c.close()

    def expired_handler(request: httpx.Request) -> httpx.Response:
        return _ok(True)

    c2 = QQClient("http://qq", transport=httpx.MockTransport(expired_handler))
    c2.set_cookie("musicid=123; musickey=mk")
    assert (await c2.login_status())["logged_in"] is False
    await c2.close()

    c3 = QQClient("http://qq", transport=httpx.MockTransport(handler))
    assert (await c3.login_status())["logged_in"] is False  # 无 cookie
    await c3.close()


async def test_user_playlists_normalization():
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok({"total": 2, "playlists": [
            {"id": 9, "dirid": 1, "title": "我喜欢", "songnum": 5},
            {"id": 10, "dirid": 2, "title": "车载", "songnum": 30}]})

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    pls = await c.user_playlists("123")
    assert pls == [{"id": "9", "name": "我喜欢", "trackCount": 5},
                   {"id": "10", "name": "车载", "trackCount": 30}]
    await c.close()


async def test_playlist_tracks_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/songlist/9/detail"
        page = int(request.url.params["page"])
        n = 100 if page == 1 else 1
        songs = [{"mid": f"mid{i:03d}", "name": f"歌{i}",
                  "singer": [{"name": "周杰伦"}], "album": {"name": "叶惠美"}}
                 for i in range(n)]
        return _ok({"songs": songs, "total": 101, "size": n})

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    tracks = await c.playlist_tracks("9")
    assert len(tracks) == 101
    assert tracks[0] == {"id": "mid000", "name": "歌0",
                         "artist": "周杰伦", "album": "叶惠美"}
    await c.close()


async def test_song_urls_cdn_and_quality():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/song/get_cdn_dispatch":
            return _ok({"sip": ["https://cdn.example/"]})
        if request.url.path == "/song/get_song_urls":
            body = json.loads(request.content)
            assert body["file_type"] == 12  # 320 → SONG_FILE_TYPES 索引
            assert body["file_info"] == [{"mid": "m1"}, {"mid": "m2"}]
            return _ok({"data": [
                {"mid": "m1", "purl": "a.mp3", "filename": "m1.mp3", "result": 0},
                {"mid": "m2", "purl": "", "filename": "", "result": 104003}]})
        return httpx.Response(404)

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    c.set_cookie("musicid=1; musickey=k")
    urls = await c.song_urls(["m1", "m2"], "320")
    assert urls["m1"] == {"url": "https://cdn.example/a.mp3", "type": "mp3", "br": 0}
    assert urls["m2"]["url"] is None
    await c.close()


async def test_error_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": -1, "msg": "风控", "data": None})

    c = QQClient("http://qq", transport=httpx.MockTransport(handler))
    with pytest.raises(QQError, match="风控"):
        await c.qr_start()
    await c.close()
