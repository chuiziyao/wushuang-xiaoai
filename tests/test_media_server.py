"""媒体流端点测试：200 全量 / 206 Range / 404 越界。"""

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wushuang.config import Settings
from wushuang.music.server import build_media_router
from wushuang.storage import Storage


@pytest.fixture
async def env(tmp_path):
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    audio = music_dir / "test.mp3"
    audio.write_bytes(b"0123456789" * 100)  # 1000 bytes
    storage = Storage(tmp_path / "t.db")
    await storage.open()
    tid = await storage.upsert_track("测试歌曲", str(audio))
    settings = Settings(data_dir=tmp_path, music_dir=music_dir)
    app = FastAPI()
    app.include_router(build_media_router())
    app.state.storage = storage
    app.state.settings = settings
    yield TestClient(app), tid, audio
    await storage.close()


def test_full_download(env):
    client, tid, audio = env
    resp = client.get(f"/media/{tid}")
    assert resp.status_code == 200
    assert resp.content == audio.read_bytes()
    assert resp.headers["accept-ranges"] == "bytes"


def test_range_request(env):
    client, tid, _ = env
    resp = client.get(f"/media/{tid}", headers={"Range": "bytes=100-199"})
    assert resp.status_code == 206
    assert len(resp.content) == 100
    assert resp.content == b"0123456789" * 10
    assert resp.headers["content-range"].startswith("bytes 100-199/")


def test_suffix_range(env):
    client, tid, _ = env
    resp = client.get(f"/media/{tid}", headers={"Range": "bytes=-50"})
    assert resp.status_code == 206
    assert len(resp.content) == 50


def test_missing_track(env):
    client, _, _ = env
    assert client.get("/media/99999").status_code == 404


def test_path_traversal_blocked(env, tmp_path):
    """索引被篡改指向音乐目录之外时，必须拒绝服务。"""
    client, tid, _ = env
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"x")
    conn = sqlite3.connect(client.app.state.storage.db_path)
    conn.execute("UPDATE tracks SET path=? WHERE id=?", (str(outside), tid))
    conn.commit()
    conn.close()
    assert client.get(f"/media/{tid}").status_code == 404
