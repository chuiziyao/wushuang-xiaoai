"""酷狗客户端测试：MockTransport 模拟 KuGouMusicApi sidecar 响应。"""

import httpx
import pytest

from wushuang.music.kugou.client import KugouClient, KugouError


async def test_qr_flow_states():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        assert "timestamp" in request.url.params  # 防 sidecar 2 分钟缓存
        if p == "/login/qr/key":
            return httpx.Response(200, json={"status": 1, "data": {"qrcode": "k1"}})
        if p == "/login/qr/create":
            assert request.url.params["key"] == "k1"
            return httpx.Response(200, json={"status": 1, "data": {
                "url": "https://h5.kugou.com/x?qrcode=k1",
                "base64": "data:image/png;base64,xx"}})
        if p == "/login/qr/check":
            calls["n"] += 1
            seq = {1: {"status": 1}, 2: {"status": 2},
                   3: {"status": 4, "token": "tok", "userid": 42},
                   4: {"status": 0}}
            return httpx.Response(200, json={"status": 1, "data": seq[calls["n"]]})
        return httpx.Response(404)

    c = KugouClient("http://kugou", transport=httpx.MockTransport(handler))
    start = await c.qr_start()
    assert start["key"] == "k1" and start["qrimg"].startswith("data:")
    assert (await c.qr_check("k1"))["state"] == "waiting"
    assert (await c.qr_check("k1"))["state"] == "scanned"
    done = await c.qr_check("k1")
    assert done["state"] == "done"
    assert done["cookie"] == "token=tok; userid=42"
    assert (await c.qr_check("k1"))["state"] == "expired"
    await c.close()


async def test_login_status():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Cookie") == "token=tok; userid=42"
        return httpx.Response(200, json={"status": 1, "data": {
            "userid": 42, "nickname": "酷狗用户"}})

    c = KugouClient("http://kugou", transport=httpx.MockTransport(handler))
    c.set_cookie("token=tok; userid=42")
    info = await c.login_status()
    assert info == {"logged_in": True, "uid": "42", "nickname": "酷狗用户"}
    await c.close()

    def bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": 0, "error_code": 20001, "data": {}})

    c2 = KugouClient("http://kugou", transport=httpx.MockTransport(bad_handler))
    c2.set_cookie("token=bad; userid=42")
    assert (await c2.login_status())["logged_in"] is False
    await c2.close()


async def test_user_playlists_pagination():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        n = 100 if page == 1 else 1
        items = [{"global_collection_id": f"collection_3_{i}_0",
                  "name": f"歌单{i}", "count": i} for i in range(n)]
        return httpx.Response(200, json={"status": 1, "data": {"list": items}})

    c = KugouClient("http://kugou", transport=httpx.MockTransport(handler))
    pls = await c.user_playlists("42")
    assert len(pls) == 101
    assert pls[0] == {"id": "collection_3_0_0", "name": "歌单0", "trackCount": 0}
    await c.close()


async def test_playlist_tracks_normalization():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "collection_3_1_0"
        page = int(request.url.params["page"])
        n = 100 if page == 1 else 1
        songs = [{"hash": f"ABC{i:03d}", "name": f"歌{i}",
                  "singerinfo": [{"name": "周杰伦"}, {"name": "费玉清"}],
                  "albuminfo": {"name": "叶惠美"}} for i in range(n)]
        return httpx.Response(200, json={"status": 1, "data": {"songs": songs}})

    c = KugouClient("http://kugou", transport=httpx.MockTransport(handler))
    tracks = await c.playlist_tracks("collection_3_1_0")
    assert len(tracks) == 101
    assert tracks[0] == {"id": "abc000", "name": "歌0",
                         "artist": "周杰伦, 费玉清", "album": "叶惠美"}
    await c.close()


async def test_song_urls_variants():
    def handler(request: httpx.Request) -> httpx.Response:
        h = request.url.params["hash"]
        assert request.url.params["quality"] == "320"
        if h == "aaa":
            return httpx.Response(200, json={"status": 1, "data": {
                "url": "http://cdn/a.mp3", "extName": "mp3", "bitrate": 320}})
        if h == "bbb":
            return httpx.Response(200, json={"status": 1, "data": {
                "url": ["http://cdn/b.flac", "http://cdn2/b.flac"],
                "extName": "flac", "bitrate": 999}})
        return httpx.Response(200, json={"status": 1, "data": {"url": ""}})

    c = KugouClient("http://kugou", transport=httpx.MockTransport(handler))
    urls = await c.song_urls(["AAA", "bbb", "ccc"], "320")
    assert urls["aaa"]["url"] == "http://cdn/a.mp3"
    assert urls["bbb"]["url"] == "http://cdn/b.flac" and urls["bbb"]["type"] == "flac"
    assert urls["ccc"]["url"] is None
    await c.close()


async def test_unreachable_sidecar():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    c = KugouClient("http://kugou", transport=httpx.MockTransport(handler))
    with pytest.raises(KugouError):
        await c.qr_start()
    await c.close()
