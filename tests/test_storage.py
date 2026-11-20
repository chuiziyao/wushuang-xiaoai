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
                                     artist="周杰伦", playlist_id=pid, netease_id="186016")
    assert (await storage.track_by_netease_id("186016"))["id"] == tid
    # 重复 path upsert 不产生新行
    tid2 = await storage.upsert_track("晴天(新)", "/m/netease/x/周杰伦 - 晴天.mp3",
                                      artist="周杰伦", playlist_id=pid, netease_id="186016")
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


async def test_sessions(storage):
    await storage.create_session("tok", "2999-01-01T00:00:00+00:00")
    assert await storage.get_session("tok")
    await storage.delete_session("tok")
    assert await storage.get_session("tok") is None
