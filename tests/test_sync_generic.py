"""通用同步引擎测试：假平台客户端 + 本地假下载，验证多平台入库/跳过/失败路径。"""

import pytest

from wushuang.config import Settings
from wushuang.crypto import SecretBox
from wushuang.music.sync import PlaylistSync
from wushuang.storage import Storage


class FakePlatform:
    """统一接口假实现：两首歌，其中一首无播放地址。"""

    name = "netease"

    def __init__(self):
        self.cookie = ""

    def set_cookie(self, cookie):
        self.cookie = cookie

    @property
    def has_cookie(self):
        return bool(self.cookie)

    async def login_status(self):
        return {"logged_in": True, "uid": "1", "nickname": "假用户"}

    async def playlist_tracks(self, remote_id):
        return [
            {"id": "101", "name": "歌A", "artist": "歌手A", "album": "专辑A"},
            {"id": "102", "name": "歌B", "artist": "歌手B", "album": ""},
            {"id": "103", "name": "无版权歌", "artist": "歌手C", "album": ""},
        ]

    async def song_urls(self, ids, level="exhigh"):
        return {
            "101": {"url": "http://fake/a.mp3", "type": "mp3", "br": 320000},
            "102": {"url": "http://fake/b.mp3", "type": "mp3", "br": 320000},
            "103": {"url": None, "type": "mp3", "br": 0},
        }

    async def close(self):
        pass


@pytest.fixture
async def env(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "test.db")
    await storage.open()
    box = SecretBox(tmp_path / "secret.key")
    settings = Settings(data_dir=tmp_path, music_dir=tmp_path / "music")
    client = FakePlatform()
    sync = PlaylistSync(storage, box, settings, {"netease": client})

    async def fake_download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-audio")

    monkeypatch.setattr(sync, "_download", fake_download)
    yield storage, box, settings, sync, client
    await sync.cancel_all()
    await storage.close()


async def test_sync_requires_login(env):
    storage, box, settings, sync, client = env
    pid = await storage.add_playlist("测试歌单", "netease", "999")
    prog = await sync.sync_playlist(pid)
    assert prog["state"] == "error" and "未登录" in prog["error"]


async def test_sync_add_skip_fail(env):
    storage, box, settings, sync, client = env
    await storage.save_music_account(
        "netease", cookie_enc=box.encrypt("MUSIC_U=x"), uid="1",
        nickname="假用户", status="ok")
    pid = await storage.add_playlist("测试歌单", "netease", "999")

    prog = await sync.sync_playlist(pid)
    assert prog["state"] == "done"
    assert prog["added"] == 2 and prog["failed"] == 1 and prog["skipped"] == 0
    assert client.cookie == "MUSIC_U=x"

    t = await storage.track_by_remote_id("netease", "101")
    assert t["title"] == "歌A" and t["source"] == "netease"
    assert t["playlist_id"] == pid
    from pathlib import Path
    assert Path(t["path"]).is_file()
    assert Path(t["path"]).parent == settings.music_dir / "netease" / "测试歌单"

    pl = await storage.get_playlist(pid)
    assert "新增 2" in pl["last_status"] and pl["last_sync_at"]

    # 二次同步：已下载跳过；无版权的仍失败
    prog2 = await sync.sync_playlist(pid)
    assert prog2["skipped"] == 2 and prog2["added"] == 0 and prog2["failed"] == 1


async def test_sync_local_playlist_rejected(env):
    storage, box, settings, sync, client = env
    pid = await storage.add_playlist("本地歌单", "local")
    prog = await sync.sync_playlist(pid)
    assert "error" in prog
