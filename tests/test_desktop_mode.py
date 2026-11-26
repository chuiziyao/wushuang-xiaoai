"""桌面版核心行为测试：模式开关、视频扫描、在线音源、推流地址与巡检。"""

import struct

import pytest

from wushuang.config import Settings
from wushuang.music.engine import MusicEngine, PlayState, _advance_decision, track_play_url
from wushuang.music.library import scan_library
from wushuang.storage import Storage


def test_desktop_flag_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("WUSHUANG_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("WUSHUANG_MUSIC_DIR", str(tmp_path / "m"))
    monkeypatch.setenv("WUSHUANG_DESKTOP", "1")
    assert Settings.from_env().desktop is True
    monkeypatch.setenv("WUSHUANG_DESKTOP", "0")
    assert Settings.from_env().desktop is False


@pytest.fixture
async def env(tmp_path):
    storage = Storage(tmp_path / "t.db")
    await storage.open()
    music = tmp_path / "music"
    music.mkdir()
    settings = Settings(data_dir=tmp_path, music_dir=music, desktop=True)
    yield storage, music, settings
    await storage.close()


def _fake_mp4() -> bytes:
    mvhd = b"\x00" * 12 + struct.pack(">II", 1000, 60000)
    moov_payload = struct.pack(">I4s", 8 + len(mvhd), b"mvhd") + mvhd
    moov = struct.pack(">I4s", 8 + len(moov_payload), b"moov") + moov_payload
    return moov


async def test_scan_video_and_online_preserved(env):
    storage, music, settings = env
    (music / "song.mp3").write_bytes(b"fake")
    (music / "movie.mp4").write_bytes(_fake_mp4())
    (music / "note.txt").write_bytes(b"x")
    online_pid = await storage.add_playlist("在线音源", "online")
    oid = await storage.upsert_track(
        "电台", "https://example.com/live.mp3",
        playlist_id=online_pid, source="online")

    # 桌面扫描：音频+视频都入库，在线音源保留
    r = await scan_library(storage, music, include_video=True)
    assert r["scanned"] == 2
    assert await storage.get_track(oid) is not None
    video = await storage.get_track_by_path(str(music / "movie.mp4"))
    assert video is not None and abs(video["duration"] - 60.0) < 1e-6

    # 音频-only 扫描（Docker 语义）：视频索引被清理，在线音源仍保留
    r2 = await scan_library(storage, music, include_video=False)
    assert r2["removed"] == 1
    assert await storage.get_track_by_path(str(music / "movie.mp4")) is None
    assert await storage.get_track(oid) is not None


async def test_track_play_url(env):
    storage, music, settings = env
    tid = await storage.upsert_track("本地", str(music / "a.mp3"))
    oid = await storage.upsert_track("在线", "https://x.example/a.mp3", source="online")
    local = await storage.get_track(tid)
    online = await storage.get_track(oid)
    assert track_play_url(settings, local) == f"{settings.speaker_base_url()}/media/{tid}"
    assert track_play_url(settings, online) == "https://x.example/a.mp3"


class FakeSpeaker:
    did = "d1"
    name = "测试音箱"

    def __init__(self):
        self.urls = []

    async def play_url(self, url):
        self.urls.append(url)
        return True

    async def play_status(self):
        return {"status": 1, "volume": 20}

    async def tts(self, text):
        pass

    async def stop(self):
        pass


async def test_engine_pushes_online_url_directly(env):
    storage, music, settings = env
    eng = MusicEngine(storage, settings)
    oid = await storage.upsert_track("电台", "https://x.example/live.mp3", source="online")
    sp = FakeSpeaker()
    ok = await eng.play_queue(sp, [await storage.get_track(oid)], "在线音源")
    assert ok
    assert sp.urls == ["https://x.example/live.mp3"]
    eng._stop_monitor("d1")


def test_online_live_no_timeout_advance():
    st = PlayState(
        tracks=[{"id": 1, "title": "电台", "artist": "", "duration": 0,
                 "path": "https://x.example/live.mp3", "source": "online"}],
        playing=True, started_at=0.0,
    )
    # 直播无时长：即使远超默认估算也不超时切歌
    assert _advance_decision(st, {"status": 1}, now=10000.0) == "wait"
    # 设备空闲仍会切歌（连续两次防抖）
    assert _advance_decision(st, {"status": 0}, now=10001.0) == "wait"
    assert _advance_decision(st, {"status": 0}, now=10002.0) == "advance"


def test_local_track_timeout_advance_unchanged():
    st = PlayState(
        tracks=[{"id": 1, "title": "歌", "artist": "", "duration": 10,
                 "path": "/m/a.mp3", "source": "local"}],
        playing=True, started_at=1.0,
    )
    assert _advance_decision(st, {"status": 1}, now=100.0) == "advance"


async def test_desktop_rejects_platform_playlist(env):
    """桌面版 API 层拒绝远端歌单（通过 app 工厂 + TestClient 验证）。"""
    from fastapi.testclient import TestClient

    from wushuang.app import create_app
    import os

    storage, music, settings = env
    os.environ["WUSHUANG_DESKTOP"] = "1"
    os.environ["WUSHUANG_DATA_DIR"] = str(settings.data_dir)
    os.environ["WUSHUANG_MUSIC_DIR"] = str(settings.music_dir)
    try:
        app = create_app()
        with TestClient(app) as client:
            # 登录拿会话
            r = client.post("/login", data={"password": "wushuang"},
                            follow_redirects=False)
            assert r.status_code == 303
            r = client.post("/api/playlists", json={
                "name": "网易云歌单", "source": "netease", "remote_id": "123"})
            assert r.status_code == 400
            assert "桌面版" in r.json()["detail"]
            # 在线音源：添加 → 列表 → 播放地址直推 → 删除
            r = client.post("/api/online", json={
                "title": "测试电台", "url": "https://x.example/live.mp3"})
            assert r.status_code == 200
            tid = r.json()["id"]
            r = client.get("/api/online")
            assert len(r.json()["tracks"]) == 1
            r = client.post("/api/online", json={
                "title": "坏地址", "url": "ftp://x/a.mp3"})
            assert r.status_code == 400
            r = client.get(f"/media/{tid}", follow_redirects=False)
            assert r.status_code == 307
            assert r.headers["location"] == "https://x.example/live.mp3"
            # 桌面版设置读写
            r = client.get("/api/desktop/config")
            assert r.status_code == 200
            r = client.post("/api/desktop/config", json={"port": 8123})
            assert r.status_code == 200 and r.json()["restart_required"]
            r = client.delete(f"/api/online/{tid}")
            assert r.status_code == 200
            assert client.get("/api/online").json()["tracks"] == []
    finally:
        os.environ.pop("WUSHUANG_DESKTOP", None)
        os.environ.pop("WUSHUANG_DATA_DIR", None)
        os.environ.pop("WUSHUANG_MUSIC_DIR", None)