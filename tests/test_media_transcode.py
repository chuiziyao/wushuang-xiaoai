"""高解析媒体流实时转码测试：判定逻辑 + 端到端（有 ffmpeg 时验证转码，无则验证回退）。"""

import os
import shutil
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3

from wushuang.music.server import _needs_transcode


def test_needs_transcode_rules():
    # PCM 无损编码 → 转码
    assert _needs_transcode({"codec_name": "pcm_s24le"}, Path("a.wav"))
    # 采样率 > 48k → 转码
    assert _needs_transcode({"codec_name": "flac", "sample_rate": "192000"},
                            Path("a.flac"))
    # 位深 > 16 → 转码
    assert _needs_transcode({"codec_name": "flac", "sample_rate": "96000",
                             "bits_per_raw_sample": "24"}, Path("a.flac"))
    # CD 规格 FLAC（44.1k/16）→ 不转码
    assert not _needs_transcode({"codec_name": "flac", "sample_rate": "44100",
                                 "bits_per_sample": "16"}, Path("a.flac"))
    # MP3 → 不转码
    assert not _needs_transcode({"codec_name": "mp3", "sample_rate": "44100"},
                                Path("a.mp3"))
    # 无 ffprobe 信息：按扩展名兜底，wav 转码、mp3 不转码
    assert _needs_transcode({}, Path("a.wav"))
    assert not _needs_transcode({}, Path("a.mp3"))


def _make_hires_wav(path: Path, seconds: float = 2.0, rate: int = 96000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(seconds * rate)
        w.writeframes(b"\x01\x80" * frames * 2)


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
    """默认关闭：高解析 WAV 原样流式（Content-Length=文件大小、支持 Range）。"""
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
    # 转码结果缓存为 MP3 文件后以普通方式推流：audio/mpeg + Content-Length + 支持 Range
    assert resp.headers["content-type"].startswith("audio/mpeg")
    assert resp.headers.get("accept-ranges") == "bytes"
    assert int(resp.headers["content-length"]) == len(resp.content)
    assert len(resp.content) > 0
    assert len(resp.content) < (tmp_path / "music" / "hi.wav").stat().st_size
    # 二次请求命中缓存：Range 可用（音箱拖动/续播）
    r2 = client.get(f"/media/{tid}", headers={"Range": "bytes=0-1023"})
    assert r2.status_code == 206 and len(r2.content) == 1024


def test_hires_transcode_respects_off_switch(client, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("需要 ffmpeg")
    _make_hires_wav(tmp_path / "music" / "hi.wav")
    client.post("/api/library/scan")
    tid = client.get("/api/library/browse?mode=all").json()["tracks"][0]["id"]
    client.post("/api/settings", json={"key": "media_transcode_hires", "value": "true"})
    client.post("/api/settings", json={"key": "media_transcode_hires", "value": "false"})
    resp = client.get(f"/media/{tid}")
    # 关闭后原样流：Content-Length 等于文件大小、支持 Range
    size = (tmp_path / "music" / "hi.wav").stat().st_size
    assert int(resp.headers["content-length"]) == size
    assert resp.headers.get("accept-ranges") == "bytes"


def test_normal_mp3_streams_raw(client, tmp_path):
    _make_mp3(tmp_path / "music" / "ok.mp3")
    client.post("/api/library/scan")
    tid = client.get("/api/library/browse?mode=all").json()["tracks"][0]["id"]
    resp = client.get(f"/media/{tid}", headers={"Range": "bytes=0-99"})
    # 普通 mp3 不转码：走 Range 原样流，206
    assert resp.status_code == 206
    assert len(resp.content) == 100
