"""本地冒烟测试：临时目录启动完整应用，验证登录/页面/API/媒体流。

不依赖外网与真实账号。用法：python scripts/smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.TemporaryDirectory(prefix="wushuang-smoke-")
tmp = Path(_tmp.name)
os.environ["WUSHUANG_DATA_DIR"] = str(tmp / "data")
os.environ["WUSHUANG_MUSIC_DIR"] = str(tmp / "music")
os.environ["WUSHUANG_ADMIN_PASSWORD"] = "smoke-pass"
os.environ["WUSHUANG_NCM_API_URL"] = "http://127.0.0.1:1"  # 不可达，验证启动容错
os.environ["WUSHUANG_MUSIC_HOST_DIR"] = "/volume1/Music"
os.environ["WUSHUANG_KUGOU_API_URL"] = "http://127.0.0.1:1"
os.environ["WUSHUANG_QQ_API_URL"] = "http://127.0.0.1:1"
os.environ["WUSHUANG_QISHUI_API_URL"] = "http://127.0.0.1:1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from wushuang.app import app  # noqa: E402


def main() -> None:
    with TestClient(app) as client:  # 触发 lifespan 完整启动
        # 登录流程
        assert client.get("/login").status_code == 200
        r = client.post("/login", data={"password": "wrong"}, follow_redirects=False)
        assert r.status_code == 401
        r = client.post("/login", data={"password": "smoke-pass"}, follow_redirects=False)
        assert r.status_code == 303, r.text

        # 全部页面可达
        for page in ["/", "/accounts", "/devices", "/music", "/library", "/player", "/chat", "/settings"]:
            r = client.get(page)
            assert r.status_code == 200, (page, r.status_code)
        assert "无双小爱同学" in client.get("/").text

        # API：状态 / 服务商 / 歌单 / 扫描 / 媒体流
        assert client.get("/api/status").status_code == 200
        # 多平台路由：四平台均已接入；未知平台应 400；sidecar 不可达应 502
        r = client.get("/api/music/platforms")
        assert r.status_code == 200
        plats = {p["name"]: p for p in r.json()["platforms"]}
        for name in ("netease", "kugou", "qq", "qishui"):
            assert plats[name]["ready"] is True, name
        assert client.get("/api/music/bogus/qr").status_code == 400
        assert client.get("/api/music/netease/qr").status_code == 502
        assert client.get("/api/music/kugou/qr").status_code == 502
        assert client.get("/api/music/qq/qr").status_code == 502
        assert client.get("/api/music/qishui/qr").status_code == 502
        # 播放控制：曲库/歌单接口可用；音箱未登录时控制类应 400
        assert client.get("/api/player/playlists").status_code == 200
        assert client.get("/api/player/search?q=晴天").status_code == 200
        assert client.get("/api/player/tracks?page=1").status_code == 200
        assert client.get("/api/player/state?did=x").status_code == 400
        assert client.post("/api/player/native",
                           json={"did": "x", "text": "播放今天的新闻"}).status_code == 400
        assert client.post("/api/player/mode",
                           json={"did": "x", "mode": "bogus"}).status_code == 400
        assert client.post("/api/player/mode",
                           json={"did": "x", "mode": "native"}).status_code == 404
        r = client.post("/api/providers", json={
            "preset": "qwen-dashscope", "name": "冒烟千问",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "sk-test", "model": "qwen-plus", "priority": 10,
        })
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        assert client.get("/api/providers").json()["providers"][0]["has_key"] is True
        # 测试按钮（不可达 key，应返回 ok=False 而非崩溃）
        r = client.post(f"/api/providers/{pid}/test")
        assert r.status_code == 200 and r.json()["ok"] is False

        r = client.post("/api/playlists", json={"name": "冒烟歌单"})
        assert r.status_code == 200, r.text
        pl_dir = tmp / "music" / "local" / "冒烟歌单"
        assert pl_dir.is_dir()
        # 远端歌单添加后自动同步 + 改名/备注
        r = client.post("/api/playlists", json={
            "name": "冒烟远端", "source": "netease", "remote_id": "123",
            "remark": "测试备注"})
        assert r.status_code == 200 and r.json()["sync_started"] is True
        pid2 = r.json()["id"]
        r = client.post(f"/api/playlists/{pid2}/meta",
                        json={"name": "999", "remark": "车载DJ"})
        assert r.status_code == 200, r.text
        r = client.post(f"/api/playlists/{pid2}/meta", json={"name": "冒烟歌单"})
        assert r.status_code == 400  # 重名拒绝
        pls = client.get("/api/player/playlists").json()["playlists"]
        assert any(p["id"] == pid2 and p["remark"] == "车载DJ" for p in pls)
        # 本地歌单指向已有目录（宿主机路径自动映射）+ 越界拒绝
        (tmp / "music" / "existing").mkdir(parents=True, exist_ok=True)
        r = client.post("/api/playlists", json={
            "name": "已有目录歌单", "source": "local",
            "folder": "/volume1/Music/existing"})
        assert r.status_code == 200, r.text
        assert (tmp / "music" / "existing").is_dir()
        r = client.post("/api/playlists", json={
            "name": "越界歌单", "source": "local", "folder": "/etc"})
        assert r.status_code == 400
        (pl_dir / "test.mp3").write_bytes(b"ID3fake-audio" * 100)
        r = client.post("/api/library/scan")
        assert r.status_code == 200 and r.json()["scanned"] == 1, r.text
        # 曲库浏览 / 播放全部 / 歌单批量加移
        assert client.get("/api/library/browse?mode=all").status_code == 200
        assert client.get("/api/library/browse?mode=folder").status_code == 200
        assert client.get("/api/library/browse?mode=album").status_code == 200
        assert client.get("/api/library/browse?mode=artist").status_code == 200
        assert client.get("/api/library/search?q=test").status_code == 200
        assert client.post("/api/library/play_all",
                           json={"did": "x", "mode": "shuffle"}).status_code == 400
        r = client.post("/api/playlists", json={"name": "自建烟测", "source": "local"})
        pid3 = r.json()["id"]
        r = client.post(f"/api/playlists/{pid3}/tracks", json={"track_ids": [1]})
        assert r.status_code == 200 and r.json()["changed"] == 1
        r = client.request("DELETE", f"/api/playlists/{pid3}/tracks",
                           json={"track_ids": [1]})
        assert r.status_code == 200 and r.json()["changed"] == 1

        r = client.get("/media/1")
        assert r.status_code == 200 and len(r.content) == 1300
        r = client.get("/media/1", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and len(r.content) == 100

        # 设置与密码
        r = client.post("/api/settings", json={"key": "poll_interval", "value": "2.0"})
        assert r.status_code == 200
        r = client.post("/api/password", json={"old": "smoke-pass", "new": "new-pass-123"})
        assert r.status_code == 200

        # 未登录访问 API 应 401
        anon = TestClient(app)
        assert anon.get("/api/status").status_code == 401

    print("SMOKE_OK")


if __name__ == "__main__":
    main()
