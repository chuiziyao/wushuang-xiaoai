"""高解析媒体流转码测试：判定逻辑 + 端到端（默认关闭 / 开启缓存转码 / CD不转）。"""

import shutil
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3

from wushuang.music.transcode import should_transcode


def test_should_transcode_rules():
    # PCM 容器（wav）一律转码，哪怕只是 44.1/16
    assert should_transcode(Path("a.wav"), 40_000_000, 233)
    # 高解析 flac：估算码率 > 1.8Mbps → 转码
    assert should_transcode(Path("a.flac"), 150_000_000, 233)
    # CD 规格 flac（~0.9Mbps）→ 不转
    assert not should_transcode(Path("a.flac"), 30_000_000, 270)
    # 320k mp3（~0.3Mbps）→ 不转
    assert not should_transcode(Path("a.mp3"), 10_000_000, 270)
    # 时长缺失时：非 PCM 容器不转（保守，避免误转大文件）
    assert not should_transcode(Path("a.flac"), 150_000_000, 0)


def _make_hires_wav(path: Path, seconds: float = 2.0, rate: int = 96000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x01\x80" * int(seconds * rate) * 2)


def _make_mp3(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 413
    path.write_bytes(frame * 12)
    ID3().save(str(path))
    t = EasyID3(str(path))
    t["title"] = ["普通mp3"]
    t.save()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WUSHUANG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WUSHUANG_MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("WUSHUANG_ADMIN_PASSWORD", "test-pass-123")
    from wushuang.app import create_app

    app = create_app()
    with TestClient(app) as c:
        c.post("/login", data={"password": "test-pass-123"}, follow_redirects=False)
        yield c


def test_hires_transcode_default_off(client, tmp_path):
    """默认关闭：高解析 WAV 原样静态流（Content-Length=大小、支持 Range）。"""
    _make_hires_wav(tmp_path / "music" / "hi.wav")
    client.post("/api/library/scan")
    tid = client.get("/api/library/browse?mode=all").json()["tracks"][0]["id"]
    resp = client.get(f"/media/{tid}")
    size = (tmp_path / "music" / "hi.wav").stat().st_size
    assert int(resp.headers["content-length"]) == size
    assert resp.headers.get("accept-ranges") == "bytes"


def test_hires_wav_transcoded_when_enabled(client, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("需要 ffmpeg")
    _make_hires_wav(tmp_path / "music" / "hi.wav")
    client.post("/api/library/scan")
    tid = client.get("/api/library/browse?mode=all").json()["tracks"][0]["id"]
    client.post("/api/settings", json={"key": "media_transcode_hires", "value": "true"})
    resp = client.get(f"/media/{tid}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.headers.get("accept-ranges") == "bytes"
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert len(resp.content) < (tmp_path / "music" / "hi.wav").stat().st_size
    # 命中缓存后 Range 可用（音箱拖动/续播）
    r2 = client.get(f"/media/{tid}", headers={"Range": "bytes=0-1023"})
    assert r2.status_code == 206 and len(r2.content) == 1024


def test_normal_mp3_streams_raw(client, tmp_path):
    _make_mp3(tmp_path / "music" / "ok.mp3")
    client.post("/api/library/scan")
    tid = client.get("/api/library/browse?mode=all").json()["tracks"][0]["id"]
    resp = client.get(f"/media/{tid}", headers={"Range": "bytes=0-99"})
    assert resp.status_code == 206
    assert len(resp.content) == 100
