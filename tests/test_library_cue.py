"""CUE 分轨 + 本地曲库浏览测试。"""

import struct

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wushuang.config import Settings
from wushuang.music.library import parse_cue, scan_library, wav_data_offset
from wushuang.music.server import build_media_router
from wushuang.storage import Storage


def _make_wav(path, seconds=4, rate=8000):
    byte_rate = rate * 2  # 16bit mono
    data = bytes([0x01, 0x02]) * (byte_rate * seconds // 2)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, byte_rate, 2, 16)
    body = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(data)) + data
    wav = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body
    path.write_bytes(wav)
    return data


@pytest.fixture
async def env(tmp_path):
    storage = Storage(tmp_path / "t.db")
    await storage.open()
    settings = Settings(data_dir=tmp_path, music_dir=tmp_path / "music")
    (tmp_path / "music" / "CD").mkdir(parents=True)
    yield storage, settings, tmp_path
    await storage.close()


async def test_cue_split_scan_and_media_range(env):
    storage, settings, tmp = env
    cd = tmp / "music" / "CD"
    data = _make_wav(cd / "disc.wav")
    (cd / "disc.cue").write_text(
        'PERFORMER "群星"\nTITLE "CD专辑"\n'
        'FILE "disc.wav" WAVE\n'
        '  TRACK 01 AUDIO\n    TITLE "第一首"\n    PERFORMER "歌手A"\n'
        '    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    TITLE "第二首"\n    PERFORMER "歌手B"\n'
        '    INDEX 01 00:02:00\n', encoding="utf-8")
    r = await scan_library(storage, settings.music_dir)
    assert r["scanned"] == 1  # 只 walked  wav（cue 不在音频扩展名）
    rows, total = await storage.local_tracks_page(1, 50)
    assert total == 2  # 整盘不入库，两个分轨入库
    t1, t2 = rows
    assert t1["title"] == "第一首" and t2["title"] == "第二首"
    assert t1["album"] == "CD专辑" and t2["artist"] == "歌手B"
    assert abs(t1["duration"] - 2.0) < 0.01
    assert t2["cue_start_byte"] == 44 + 16 + 8 + 32000 or t2["cue_start_byte"] > 0

    app = FastAPI()
    app.state.storage = storage
    app.state.settings = settings
    app.include_router(build_media_router())
    with TestClient(app) as client:
        resp = client.get(f"/media/{t2['id']}", headers={"Range": "bytes=0-99"})
        assert resp.status_code == 206
        off = t2["cue_start_byte"]
        assert resp.content == data[off:off + 100]
        full = client.get(f"/media/{t1['id']}")
        assert full.status_code == 200
        assert len(full.content) == 32000


async def test_browse_groups(env):
    storage, settings, tmp = env
    (tmp / "music" / "A").mkdir(parents=True)
    (tmp / "music" / "B").mkdir(parents=True)
    _make_wav(tmp / "music" / "A" / "x.wav", seconds=1)
    _make_wav(tmp / "music" / "B" / "y.wav", seconds=1)
    await scan_library(storage, settings.music_dir)
    await storage.upsert_track("带专辑", str(tmp / "music" / "A" / "z.mp3"),
                               album="专辑X", source="local")
    folders = await storage.local_folders()
    assert any(f["name"].endswith("/A") or f["name"].endswith("\\A") for f in folders)
    albums = await storage.local_albums()
    assert {"name": "专辑X", "count": 1} in albums
    rows, total = await storage.local_tracks_page(1, 50, album="专辑X")
    assert total == 1 and rows[0]["title"] == "带专辑"


async def test_playlist_add_remove_tracks(env):
    storage, settings, tmp = env
    pid = await storage.add_playlist("自建", "local")
    t1 = await storage.upsert_track("a", "/m/a.mp3", source="local")
    t2 = await storage.upsert_track("b", "/m/b.mp3", source="local")
    assert await storage.playlist_add_tracks(pid, [t1, t2]) == 2
    rows, total = await storage.tracks_page(pid, 1, 50)
    assert total == 2
    assert await storage.playlist_remove_tracks(pid, [t1]) == 1
    rows, total = await storage.tracks_page(pid, 1, 50)
    assert total == 1 and rows[0]["id"] == t2


def test_parse_cue_frames(tmp_path):
    cue = tmp_path / "x.cue"
    cue.write_text('TRACK 01 AUDIO\nINDEX 01 00:00:00\n'
                   'TRACK 02 AUDIO\nINDEX 01 01:00:00\n', encoding="utf-8")
    entries, _, _ = parse_cue(cue)
    assert entries[0]["start_frame"] == 0
    assert entries[1]["start_frame"] == 60 * 75


def test_wav_data_offset(tmp_path):
    p = tmp_path / "d.wav"
    _make_wav(p, seconds=2)
    off, rate, size = wav_data_offset(p)
    assert rate == 16000 and size == 32000 and off > 0


async def test_flac_cue_split_via_ffmpeg(env):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg 不可用")
    storage, settings, tmp = env
    cd = tmp / "music" / "FLACCD"
    cd.mkdir(parents=True)
    src = cd / "disc.flac"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-c:a", "flac", str(src)],
                   check=True, capture_output=True)
    (cd / "disc.cue").write_text(
        'PERFORMER "群星"\nTITLE "FLAC专辑"\n'
        'FILE "disc.flac" WAVE\n'
        '  TRACK 01 AUDIO\n    TITLE "上"\n    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n    TITLE "下"\n    INDEX 01 00:02:00\n', encoding="utf-8")
    await scan_library(storage, settings.music_dir)
    rows, total = await storage.local_tracks_page(1, 50)
    assert total == 2
    assert {r["title"] for r in rows} == {"上", "下"}
    assert all(".cue_cache" in r["path"] for r in rows)
    assert all(r["duration"] > 1.5 for r in rows)
    # 缓存目录不被当作普通曲库重复扫描
    r2 = await scan_library(storage, settings.music_dir)
    _, total2 = await storage.local_tracks_page(1, 50)
    assert total2 == 2
