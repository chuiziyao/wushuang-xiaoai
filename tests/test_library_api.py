"""曲库 Web API 端到端测试：扫描 → 分组浏览 → 一键生成同名歌单 → 加入/移除。

用真实带标签 MP3（合成静音帧 + EasyID3）走完整扫描链路，
验证多对多歌单成员关系在 API 层的行为（修复"新建歌单无法加歌"的回归）。
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.easyid3 import EasyID3
from mutagen.id3 import ID3


def _make_mp3(path: Path, title: str, artist: str = "", album: str = ""):
    """合成可被 mutagen 读取标签的最小 MP3（MPEG1 Layer3 静音帧）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 413
    path.write_bytes(frame * 12)
    ID3().save(str(path))
    tags = EasyID3(str(path))
    tags["title"] = [title]
    if artist:
        tags["artist"] = [artist]
    if album:
        tags["album"] = [album]
    tags.save()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WUSHUANG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WUSHUANG_MUSIC_DIR", str(tmp_path / "music"))
    monkeypatch.setenv("WUSHUANG_ADMIN_PASSWORD", "test-pass-123")
    from wushuang.app import create_app

    app = create_app()
    with TestClient(app) as c:
        r = c.post("/login", data={"password": "test-pass-123"},
                   follow_redirects=False)
        assert r.status_code == 303
        yield c


@pytest.fixture
def seeded(client, tmp_path):
    music = tmp_path / "music"
    _make_mp3(music / "评书/单田芳/01.mp3", "第一回", artist="单田芳", album="三国演义")
    _make_mp3(music / "评书/单田芳/02.mp3", "第二回", artist="单田芳", album="三国演义")
    _make_mp3(music / "歌曲/晴天.mp3", "晴天", artist="周杰伦", album="叶惠美")
    r = client.post("/api/library/scan")
    assert r.status_code == 200 and r.json()["scanned"] == 3
    return client


def test_browse_groups(seeded):
    r = seeded.get("/api/library/browse?mode=artist").json()
    groups = {g["name"]: g["count"] for g in r["groups"]}
    assert groups == {"单田芳": 2, "周杰伦": 1}
    r2 = seeded.get("/api/library/browse?mode=album").json()
    assert {g["name"]: g["count"] for g in r2["groups"]} == {"三国演义": 2, "叶惠美": 1}
    r3 = seeded.get("/api/library/browse?mode=artist&artist=单田芳").json()
    assert r3["total"] == 2


def test_make_playlist_idempotent(seeded):
    body = {"kind": "artist", "name": "单田芳"}
    r = seeded.post("/api/library/make_playlist", json=body).json()
    assert r["created"] is True and r["total"] == 2 and r["added"] == 2
    pid = r["id"]
    # 歌单视图可见全部成员
    view = seeded.get(f"/api/library/browse?mode=playlist&playlist_id={pid}").json()
    assert view["total"] == 2
    # 重复点击：不报错、不重复加入
    r2 = seeded.post("/api/library/make_playlist", json=body).json()
    assert r2["created"] is False and r2["added"] == 0 and r2["id"] == pid
    counts = {p["name"]: p["count"]
              for p in seeded.get("/api/player/playlists").json()["playlists"]}
    assert counts["单田芳"] == 2


def test_make_playlist_album_and_folder(seeded, tmp_path):
    r = seeded.post("/api/library/make_playlist",
                    json={"kind": "album", "name": "三国演义"}).json()
    assert r["created"] and r["total"] == 2 and r["name"] == "三国演义"
    folder = (tmp_path / "music" / "评书" / "单田芳").as_posix()
    r2 = seeded.post("/api/library/make_playlist",
                     json={"kind": "folder", "name": folder}).json()
    # 文件夹歌单取目录名；与已有同名歌单合并（幂等）
    assert r2["name"] == "单田芳" and r2["total"] == 2


def test_add_to_new_playlist_flow(seeded):
    """回归：选中歌曲 → 新建歌单 → 加入成功，且不影响其他歌单/曲库视图。"""
    # 先给歌手生成歌单
    artist_pl = seeded.post("/api/library/make_playlist",
                            json={"kind": "artist", "name": "单田芳"}).json()["id"]
    tracks = seeded.get("/api/library/browse?mode=all").json()["tracks"]
    ids = [t["id"] for t in tracks if t["artist"] == "单田芳"]
    assert len(ids) == 2
    # 新建空歌单（前端修复后不再切换视图；API 层验证加入空歌单可行）
    new_pl = seeded.post("/api/playlists",
                         json={"name": "666", "source": "local", "remark": "评书精选"}).json()["id"]
    assert seeded.get(f"/api/library/browse?mode=playlist&playlist_id={new_pl}").json()["total"] == 0
    r = seeded.post(f"/api/playlists/{new_pl}/tracks", json={"track_ids": ids}).json()
    assert r["changed"] == 2
    assert seeded.get(f"/api/library/browse?mode=playlist&playlist_id={new_pl}").json()["total"] == 2
    # 多对多：歌手歌单成员不受影响；全部本地视图仍在
    assert seeded.get(f"/api/library/browse?mode=playlist&playlist_id={artist_pl}").json()["total"] == 2
    assert seeded.get("/api/library/browse?mode=all").json()["total"] == 3
    # 从新歌单移除 → 歌手歌单仍在
    r2 = seeded.request("DELETE", f"/api/playlists/{new_pl}/tracks",
                        json={"track_ids": ids}).json()
    assert r2["changed"] == 2
    assert seeded.get(f"/api/library/browse?mode=playlist&playlist_id={artist_pl}").json()["total"] == 2
    # 重新扫描不冲掉手动成员关系
    seeded.post("/api/playlists/" + str(new_pl) + "/tracks", json={"track_ids": ids})
    seeded.post("/api/library/scan")
    assert seeded.get(f"/api/library/browse?mode=playlist&playlist_id={new_pl}").json()["total"] == 2


def test_play_group_validation(seeded):
    # 空分组 → 400
    r = seeded.post("/api/library/play_group",
                    json={"did": "d1", "kind": "artist", "name": "不存在"})
    assert r.status_code == 400
    # 有歌但未登录小米 → 400（提示扫码），路由与参数链路正确
    r2 = seeded.post("/api/library/play_group",
                     json={"did": "d1", "kind": "artist", "name": "单田芳"})
    assert r2.status_code == 400 and "小米" in r2.json()["detail"]
    # 非法 kind → 400
    r3 = seeded.post("/api/library/play_group",
                     json={"did": "d1", "kind": "xxx", "name": "单田芳"})
    assert r3.status_code == 400


def test_make_playlist_empty_group(seeded):
    r = seeded.post("/api/library/make_playlist",
                    json={"kind": "artist", "name": "不存在"})
    assert r.status_code == 400


def test_library_page_renders(seeded):
    r = seeded.get("/library")
    assert r.status_code == 200
    assert "加入歌单" in r.text and "生成同名歌单" in r.text


def test_api_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/library/browse?mode=all").status_code == 401
