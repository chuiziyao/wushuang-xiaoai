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


async def test_playlist_membership_many_to_many(storage):
    """一首歌可同时属于多个歌单；加入/移除互不影响（音乐播放器逻辑）。"""
    pa = await storage.add_playlist("歌单A", "local")
    pb = await storage.add_playlist("歌单B", "local")
    t1 = await storage.upsert_track("歌1", "/m/1.mp3", artist="歌手X")
    t2 = await storage.upsert_track("歌2", "/m/2.mp3", artist="歌手X")
    # 加入 A：不改变曲目归属（playlist_id 仍为 NULL）
    assert await storage.playlist_add_tracks(pa, [t1, t2]) == 2
    assert (await storage.get_track(t1))["playlist_id"] is None
    # 再加入 B：A 中仍在
    assert await storage.playlist_add_tracks(pb, [t1]) == 1
    assert {t["id"] for t in await storage.tracks_by_playlist(pa)} == {t1, t2}
    assert {t["id"] for t in await storage.tracks_by_playlist(pb)} == {t1}
    # 重复加入幂等
    assert await storage.playlist_add_tracks(pa, [t1, t2]) == 0
    # 不存在的曲目 id 被忽略
    assert await storage.playlist_add_tracks(pa, [99999]) == 0
    # 计数 = 归属 ∪ 成员
    assert await storage.playlist_track_counts() == {pa: 2, pb: 1}
    # 从 A 移除 t1：B 中仍在
    assert await storage.playlist_remove_tracks(pa, [t1]) == 1
    assert {t["id"] for t in await storage.tracks_by_playlist(pa)} == {t2}
    assert {t["id"] for t in await storage.tracks_by_playlist(pb)} == {t1}
    # 分页同样取并集
    rows, total = await storage.tracks_page(pa, 1, 10)
    assert total == 1 and rows[0]["id"] == t2


async def test_playlist_membership_with_home(storage):
    """归属（目录/同步）曲目也算成员；移除时一并清除归属。"""
    pid = await storage.add_playlist("同步歌单", "netease", "1")
    tid = await storage.upsert_track("下载歌", "/m/netease/x/a.mp3",
                                     playlist_id=pid, source="netease")
    other = await storage.add_playlist("手动歌单", "local")
    await storage.playlist_add_tracks(other, [tid])
    assert await storage.playlist_track_counts() == {pid: 1, other: 1}
    # 从归属歌单移除 → 归属清除，但手动歌单成员保留
    assert await storage.playlist_remove_tracks(pid, [tid]) == 1
    assert (await storage.get_track(tid))["playlist_id"] is None
    assert [t["id"] for t in await storage.tracks_by_playlist(other)] == [tid]
    assert await storage.tracks_by_playlist(pid) == []


async def test_delete_playlist_keeps_manual_members(storage):
    """删除歌单：归属曲目索引删除；手动加入的曲目保留并解除关系。"""
    home_pl = await storage.add_playlist("目录歌单", "local")
    manual_pl = await storage.add_playlist("手动歌单", "local")
    home_t = await storage.upsert_track("归属歌", "/m/home/a.mp3", playlist_id=home_pl)
    free_t = await storage.upsert_track("散曲", "/m/free/b.mp3")
    await storage.playlist_add_tracks(home_pl, [free_t])
    await storage.playlist_add_tracks(manual_pl, [home_t, free_t])
    await storage.delete_playlist(home_pl)
    # 归属曲目被删（含其在其他歌单的成员关系）；散曲保留且仍在手动歌单
    assert await storage.get_track(home_t) is None
    assert [t["id"] for t in await storage.tracks_by_playlist(manual_pl)] == [free_t]
    assert await storage.playlist_track_counts() == {manual_pl: 1}
    # 删除曲目时清理全部成员关系
    await storage.delete_track(free_t)
    assert await storage.playlist_track_counts() == {}


async def test_local_folder_filter_windows_paths(storage):
    """文件夹过滤兼容 Windows 反斜杠路径，且通配符被转义。"""
    await storage.upsert_track("a", "D:\\Music\\评书\\01.mp3")
    await storage.upsert_track("b", "D:\\Music\\评书\\02.mp3")
    await storage.upsert_track("c", "D:\\Music\\歌曲\\x.mp3")
    await storage.upsert_track("d", "D:\\Music\\评书100%\\y.mp3")
    rows, total = await storage.local_tracks_page(1, 50, folder="D:\\Music\\评书")
    assert total == 2
    rows2, total2 = await storage.local_tracks_page(1, 50, folder="D:/Music/评书/")
    assert total2 == 2  # 正斜杠写法等价
    by_folder = await storage.tracks_by_folder("D:\\Music\\评书")
    assert {t["title"] for t in by_folder} == {"a", "b"}
    # % 与 _ 是 LIKE 通配符，必须按字面匹配
    rows3, total3 = await storage.local_tracks_page(1, 50, folder="D:\\Music\\评书100%")
    assert total3 == 1 and rows3[0]["title"] == "d"
    groups = await storage.local_folders()
    names = {g["name"] for g in groups}
    assert "D:/Music/评书" in names  # 分组名统一 / 分隔符


async def test_tracks_by_album(storage):
    await storage.upsert_track("a", "/m/a.mp3", album="专辑1", artist="歌手1")
    await storage.upsert_track("b", "/m/b.mp3", album="专辑1", artist="歌手2")
    await storage.upsert_track("c", "/m/c.mp3", album="专辑2")
    await storage.upsert_track("net", "/m/n.mp3", album="专辑1", source="netease")
    got = await storage.tracks_by_album("专辑1")
    assert {t["title"] for t in got} == {"a", "b"}  # 仅本地


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
