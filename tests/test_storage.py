"""存储层测试（真实 SQLite，临时目录）。"""

import pytest

from wushuang.storage import Storage


@pytest.fixture
async def storage(tmp_path):
    s = Storage(tmp_path / "test.db")
    await s.open()
    yield s
    await s.close()


async def test_settings(storage):
    assert await storage.get_setting("x") is None
    assert await storage.get_setting("x", "d") == "d"
    await storage.set_setting("x", "1")
    await storage.set_setting("x", "2")
    assert await storage.get_setting("x") == "2"
    assert (await storage.all_settings())["x"] == "2"


async def test_xiaomi_account(storage):
    assert await storage.get_xiaomi_account() is None
    await storage.save_xiaomi_account("13800000000", "enc-pass")
    acc = await storage.get_xiaomi_account()
    assert acc["username"] == "13800000000"
    await storage.update_xiaomi_token("enc-token", "ok")
    acc = await storage.get_xiaomi_account()
    assert acc["token_enc"] == "enc-token" and acc["status"] == "ok"


async def test_providers(storage):
    pid = await storage.add_provider("doubao-web", "豆包免费",
                                     "http://x/v1", "enc", "doubao", priority=10)
    pid2 = await storage.add_provider("qwen-dashscope", "千问官方",
                                      "http://y/v1", "enc2", "qwen-plus", priority=20)
    enabled = await storage.list_providers(enabled_only=True)
    assert [p["id"] for p in enabled] == [pid, pid2]  # 按优先级排序
    await storage.update_provider(pid, enabled=False, priority=5)
    enabled = await storage.list_providers(enabled_only=True)
    assert [p["id"] for p in enabled] == [pid2]
    await storage.delete_provider(pid2)
    assert await storage.list_providers() == [] or len(await storage.list_providers()) == 1


async def test_devices(storage):
    await storage.upsert_device("did1", "小爱Pro", "LX06")
    await storage.upsert_device("did1", "小爱Pro改名", "LX06")
    await storage.upsert_device("did2", "小米一代", "S12")
    devices = await storage.list_devices()
    assert len(devices) == 2
    await storage.set_device_enabled("did1", True)
    await storage.set_device_config("did1", {"llm_triggers": ["请"]})
    d = await storage.get_device("did1")
    assert d["enabled"] == 1 and d["name"] == "小爱Pro改名"
    assert '"请"' in d["config_json"]


async def test_playlists_and_tracks(storage):
    pid = await storage.add_playlist("我喜欢的音乐", "netease", "123456", "/m/netease/x", "0 4 * * *")
    assert (await storage.get_playlist_by_name("我喜欢的音乐"))["id"] == pid
    tid = await storage.upsert_track("晴天", "/m/netease/x/周杰伦 - 晴天.mp3",
                                     artist="周杰伦", playlist_id=pid, remote_id="186016",
                                     source="netease")
    assert (await storage.track_by_remote_id("netease", "186016"))["id"] == tid
    # 重复 path upsert 不产生新行
    tid2 = await storage.upsert_track("晴天(新)", "/m/netease/x/周杰伦 - 晴天.mp3",
                                      artist="周杰伦", playlist_id=pid, remote_id="186016",
                                      source="netease")
    assert tid2 == tid
    assert (await storage.get_track(tid))["title"] == "晴天(新)"
    found = await storage.search_tracks("晴天")
    assert len(found) == 1
    found = await storage.search_tracks("周杰伦")
    assert len(found) == 1
    assert await storage.count_tracks() == 1
    await storage.delete_playlist(pid)
    assert await storage.count_tracks() == 0


async def test_chat_logs(storage):
    await storage.add_chat_log("did1", "问题1", "回答1", "豆包", 1200, "ok")
    await storage.add_chat_log("did2", "问题2", "", "", 0, "failed")
    logs = await storage.recent_chat_logs()
    assert len(logs) == 2 and logs[0]["query"] == "问题2"  # DESC
    logs = await storage.recent_chat_logs("did1")
    assert len(logs) == 1
    await storage.clear_chat_logs("did1")
    assert len(await storage.recent_chat_logs()) == 1
    await storage.clear_chat_logs()
    assert len(await storage.recent_chat_logs()) == 0


async def test_playlist_remark_and_rename(storage):
    pid = await storage.add_playlist("666", "netease", "1", remark="祁厅长DJ合集")
    pl = await storage.get_playlist(pid)
    assert pl["remark"] == "祁厅长DJ合集"
    await storage.update_playlist(pid, name="777", remark="车载DJ")
    pl = await storage.get_playlist(pid)
    assert pl["name"] == "777" and pl["remark"] == "车载DJ"
    assert await storage.get_playlist_by_name("666") is None


async def test_sessions(storage):
    await storage.create_session("tok", "2999-01-01T00:00:00+00:00")
    assert await storage.get_session("tok")
    await storage.delete_session("tok")
    assert await storage.get_session("tok") is None


async def test_music_accounts(storage):
    assert await storage.get_music_account("netease") is None
    await storage.save_music_account("netease", cookie_enc="enc1", uid="42",
                                     nickname="锤无双", status="ok")
    await storage.save_music_account("kugou", cookie_enc="enc2", uid="7",
                                     nickname="酷狗用户", status="ok")
    acc = await storage.get_music_account("netease")
    assert acc["uid"] == "42" and acc["nickname"] == "锤无双"
    # 部分更新：COALESCE 保留原值
    await storage.save_music_account("netease", status="expired")
    acc = await storage.get_music_account("netease")
    assert acc["status"] == "expired" and acc["cookie_enc"] == "enc1"
    accounts = await storage.list_music_accounts()
    assert set(accounts) == {"netease", "kugou"}


async def test_legacy_netease_migration(tmp_path):
    """旧版 netease_account / netease_id 数据库 → 多平台 schema，幂等。"""
    import sqlite3

    db = tmp_path / "legacy.db"
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE netease_account(
      id INTEGER PRIMARY KEY CHECK(id=1), cookie_enc TEXT, uid TEXT,
      nickname TEXT, status TEXT DEFAULT 'logged_out', updated_at TEXT);
    CREATE TABLE playlists(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
      source TEXT NOT NULL DEFAULT 'local', netease_id TEXT, folder TEXT,
      cron TEXT DEFAULT '', enabled INTEGER DEFAULT 1, last_sync_at TEXT,
      last_status TEXT DEFAULT '', created_at TEXT);
    CREATE TABLE tracks(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
      artist TEXT DEFAULT '', album TEXT DEFAULT '', path TEXT NOT NULL UNIQUE,
      duration REAL DEFAULT 0, playlist_id INTEGER, netease_id TEXT,
      source TEXT DEFAULT 'local', created_at TEXT);
    CREATE INDEX idx_tracks_netease ON tracks(netease_id);
    INSERT INTO netease_account VALUES(1,'enc-old','42','旧昵称','ok','2026-01-01T00:00:00+00:00');
    INSERT INTO playlists(name,source,netease_id) VALUES('旧歌单','netease','777');
    INSERT INTO tracks(title,path,netease_id,source) VALUES('旧歌','/m/x.mp3','888','netease');
    """)
    con.commit()
    con.close()

    s = Storage(db)
    await s.open()
    acc = await s.get_music_account("netease")
    assert acc["cookie_enc"] == "enc-old" and acc["uid"] == "42"
    pl = await s.get_playlist_by_name("旧歌单")
    assert pl["remote_id"] == "777"
    assert pl["remark"] == ""  # 旧库自动补 remark 列
    track = await s.track_by_remote_id("netease", "888")
    assert track["title"] == "旧歌"
    await s.close()

    # 二次打开不重复迁移、不报错
    s2 = Storage(db)
    await s2.open()
    assert (await s2.get_music_account("netease"))["uid"] == "42"
    assert (await s2.get_playlist_by_name("旧歌单"))["remote_id"] == "777"
    await s2.close()
