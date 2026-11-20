"""SQLite 存储层：设置、账号、LLM 服务商、设备、歌单、曲目、日志、会话。

所有敏感字段（密码/Cookie/API Key/小米 token）以 *_enc 列存放，
由 crypto.SecretBox 加密，明文永不落盘。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS xiaomi_account(
  id INTEGER PRIMARY KEY CHECK(id=1),
  username TEXT NOT NULL, password_enc TEXT NOT NULL,
  token_enc TEXT, status TEXT DEFAULT 'unknown', updated_at TEXT);

CREATE TABLE IF NOT EXISTS netease_account(
  id INTEGER PRIMARY KEY CHECK(id=1),
  cookie_enc TEXT, uid TEXT, nickname TEXT,
  status TEXT DEFAULT 'logged_out', updated_at TEXT);

CREATE TABLE IF NOT EXISTS llm_providers(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  preset TEXT NOT NULL, name TEXT NOT NULL,
  base_url TEXT NOT NULL, api_key_enc TEXT, model TEXT NOT NULL,
  enabled INTEGER DEFAULT 1, priority INTEGER DEFAULT 100,
  extra_json TEXT DEFAULT '{}', updated_at TEXT);

CREATE TABLE IF NOT EXISTS devices(
  did TEXT PRIMARY KEY, name TEXT, hardware TEXT, model TEXT,
  enabled INTEGER DEFAULT 0, config_json TEXT DEFAULT '{}', updated_at TEXT);

CREATE TABLE IF NOT EXISTS playlists(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE, source TEXT NOT NULL DEFAULT 'local',
  netease_id TEXT, folder TEXT, cron TEXT DEFAULT '',
  enabled INTEGER DEFAULT 1, last_sync_at TEXT, last_status TEXT DEFAULT '',
  created_at TEXT);

CREATE TABLE IF NOT EXISTS tracks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL, artist TEXT DEFAULT '', album TEXT DEFAULT '',
  path TEXT NOT NULL UNIQUE, duration REAL DEFAULT 0,
  playlist_id INTEGER, netease_id TEXT, source TEXT DEFAULT 'local',
  created_at TEXT);
CREATE INDEX IF NOT EXISTS idx_tracks_playlist ON tracks(playlist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_netease ON tracks(netease_id);

CREATE TABLE IF NOT EXISTS chat_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  did TEXT, ts TEXT, query TEXT, answer TEXT,
  provider TEXT, latency_ms INTEGER, status TEXT);
CREATE INDEX IF NOT EXISTS idx_chat_did_ts ON chat_logs(did, id);

CREATE TABLE IF NOT EXISTS sync_logs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  playlist_id INTEGER, ts TEXT, added INTEGER DEFAULT 0,
  skipped INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, message TEXT);

CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, created_at TEXT, expires_at TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row(row: aiosqlite.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path, timeout=30)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=30000")
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage 尚未 open()")
        return self._db

    # ---------- 运行时设置 ----------
    async def get_setting(self, key: str, default: str | None = None) -> str | None:
        async with self.db.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self.db.commit()

    async def all_settings(self) -> dict[str, str]:
        async with self.db.execute("SELECT key,value FROM settings") as cur:
            return {r["key"]: r["value"] async for r in cur}

    # ---------- 小米账号（单账号） ----------
    async def get_xiaomi_account(self) -> dict | None:
        async with self.db.execute("SELECT * FROM xiaomi_account WHERE id=1") as cur:
            return _row(await cur.fetchone())

    async def save_xiaomi_account(
        self, username: str, password_enc: str, token_enc: str | None = None
    ) -> None:
        await self.db.execute(
            "INSERT INTO xiaomi_account(id,username,password_enc,token_enc,status,updated_at) "
            "VALUES(1,?,?,?,'unknown',?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, "
            "password_enc=excluded.password_enc, token_enc=excluded.token_enc, "
            "status='unknown', updated_at=excluded.updated_at",
            (username, password_enc, token_enc, _now()),
        )
        await self.db.commit()

    async def update_xiaomi_token(self, token_enc: str | None, status: str) -> None:
        await self.db.execute(
            "UPDATE xiaomi_account SET token_enc=?, status=?, updated_at=? WHERE id=1",
            (token_enc, status, _now()),
        )
        await self.db.commit()

    # ---------- 网易云账号（单账号） ----------
    async def get_netease_account(self) -> dict | None:
        async with self.db.execute("SELECT * FROM netease_account WHERE id=1") as cur:
            return _row(await cur.fetchone())

    async def save_netease_account(
        self,
        cookie_enc: str | None = None,
        uid: str | None = None,
        nickname: str | None = None,
        status: str | None = None,
    ) -> None:
        cur_acc = await self.get_netease_account()
        if cur_acc is None:
            await self.db.execute(
                "INSERT INTO netease_account(id,cookie_enc,uid,nickname,status,updated_at) "
                "VALUES(1,?,?,?,?,?)",
                (cookie_enc, uid, nickname, status or "logged_out", _now()),
            )
        else:
            await self.db.execute(
                "UPDATE netease_account SET cookie_enc=COALESCE(?,cookie_enc), "
                "uid=COALESCE(?,uid), nickname=COALESCE(?,nickname), "
                "status=COALESCE(?,status), updated_at=? WHERE id=1",
                (cookie_enc, uid, nickname, status, _now()),
            )
        await self.db.commit()

    # ---------- LLM 服务商 ----------
    async def list_providers(self, enabled_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM llm_providers"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY priority ASC, id ASC"
        async with self.db.execute(sql) as cur:
            return [dict(r) async for r in cur]

    async def get_provider(self, pid: int) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM llm_providers WHERE id=?", (pid,)
        ) as cur:
            return _row(await cur.fetchone())

    async def add_provider(
        self,
        preset: str,
        name: str,
        base_url: str,
        api_key_enc: str,
        model: str,
        priority: int = 100,
        enabled: bool = True,
        extra_json: str = "{}",
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO llm_providers(preset,name,base_url,api_key_enc,model,"
            "enabled,priority,extra_json,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (preset, name, base_url, api_key_enc, model, int(enabled), priority,
             extra_json, _now()),
        )
        await self.db.commit()
        return cur.lastrowid

    async def update_provider(self, pid: int, **fields: Any) -> None:
        allowed = {"preset", "name", "base_url", "api_key_enc", "model",
                   "enabled", "priority", "extra_json"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(int(v) if k == "enabled" else v)
        if not sets:
            return
        sets.append("updated_at=?")
        vals.append(_now())
        vals.append(pid)
        await self.db.execute(
            f"UPDATE llm_providers SET {','.join(sets)} WHERE id=?", vals
        )
        await self.db.commit()

    async def delete_provider(self, pid: int) -> None:
        await self.db.execute("DELETE FROM llm_providers WHERE id=?", (pid,))
        await self.db.commit()

    # ---------- 设备 ----------
    async def upsert_device(
        self, did: str, name: str, hardware: str, model: str = ""
    ) -> None:
        await self.db.execute(
            "INSERT INTO devices(did,name,hardware,model,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(did) DO UPDATE SET name=excluded.name, "
            "hardware=excluded.hardware, model=excluded.model, updated_at=excluded.updated_at",
            (did, name, hardware, model, _now()),
        )
        await self.db.commit()

    async def list_devices(self) -> list[dict]:
        async with self.db.execute("SELECT * FROM devices ORDER BY name") as cur:
            return [dict(r) async for r in cur]

    async def get_device(self, did: str) -> dict | None:
        async with self.db.execute("SELECT * FROM devices WHERE did=?", (did,)) as cur:
            return _row(await cur.fetchone())

    async def set_device_enabled(self, did: str, enabled: bool) -> None:
        await self.db.execute(
            "UPDATE devices SET enabled=?, updated_at=? WHERE did=?",
            (int(enabled), _now(), did),
        )
        await self.db.commit()

    async def set_device_config(self, did: str, config: dict) -> None:
        await self.db.execute(
            "UPDATE devices SET config_json=?, updated_at=? WHERE did=?",
            (json.dumps(config, ensure_ascii=False), _now(), did),
        )
        await self.db.commit()

    # ---------- 歌单 ----------
    async def list_playlists(self) -> list[dict]:
        async with self.db.execute("SELECT * FROM playlists ORDER BY id") as cur:
            return [dict(r) async for r in cur]

    async def get_playlist(self, pid: int) -> dict | None:
        async with self.db.execute("SELECT * FROM playlists WHERE id=?", (pid,)) as cur:
            return _row(await cur.fetchone())

    async def get_playlist_by_name(self, name: str) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM playlists WHERE name=?", (name,)
        ) as cur:
            return _row(await cur.fetchone())

    async def add_playlist(
        self,
        name: str,
        source: str = "local",
        netease_id: str = "",
        folder: str = "",
        cron: str = "",
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO playlists(name,source,netease_id,folder,cron,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (name, source, netease_id, folder, cron, _now()),
        )
        await self.db.commit()
        return cur.lastrowid

    async def update_playlist(self, pid: int, **fields: Any) -> None:
        allowed = {"name", "source", "netease_id", "folder", "cron",
                   "enabled", "last_sync_at", "last_status"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(int(v) if k == "enabled" else v)
        if not sets:
            return
        vals.append(pid)
        await self.db.execute(
            f"UPDATE playlists SET {','.join(sets)} WHERE id=?", vals
        )
        await self.db.commit()

    async def delete_playlist(self, pid: int) -> None:
        await self.db.execute("DELETE FROM tracks WHERE playlist_id=?", (pid,))
        await self.db.execute("DELETE FROM playlists WHERE id=?", (pid,))
        await self.db.commit()

    # ---------- 曲目 ----------
    async def upsert_track(
        self,
        title: str,
        path: str,
        artist: str = "",
        album: str = "",
        duration: float = 0.0,
        playlist_id: int | None = None,
        netease_id: str = "",
        source: str = "local",
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO tracks(title,artist,album,path,duration,playlist_id,"
            "netease_id,source,created_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET title=excluded.title, "
            "artist=excluded.artist, album=excluded.album, duration=excluded.duration, "
            "playlist_id=COALESCE(excluded.playlist_id,tracks.playlist_id), "
            "netease_id=COALESCE(NULLIF(excluded.netease_id,''),tracks.netease_id), "
            "source=excluded.source",
            (title, artist, album, path, duration, playlist_id, netease_id,
             source, _now()),
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT id FROM tracks WHERE path=?", (path,)
        ) as c2:
            row = await c2.fetchone()
        return row["id"]

    async def get_track(self, tid: int) -> dict | None:
        async with self.db.execute("SELECT * FROM tracks WHERE id=?", (tid,)) as cur:
            return _row(await cur.fetchone())

    async def track_by_netease_id(self, netease_id: str) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM tracks WHERE netease_id=?", (netease_id,)
        ) as cur:
            return _row(await cur.fetchone())

    async def tracks_by_playlist(self, pid: int) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM tracks WHERE playlist_id=? ORDER BY id", (pid,)
        ) as cur:
            return [dict(r) async for r in cur]

    async def search_tracks(self, keyword: str, limit: int = 20) -> list[dict]:
        like = f"%{keyword}%"
        async with self.db.execute(
            "SELECT * FROM tracks WHERE title LIKE ? OR artist LIKE ? OR album LIKE ? "
            "ORDER BY CASE WHEN title LIKE ? THEN 0 ELSE 1 END, id LIMIT ?",
            (like, like, like, like, limit),
        ) as cur:
            return [dict(r) async for r in cur]

    async def count_tracks(self) -> int:
        async with self.db.execute("SELECT COUNT(*) AS n FROM tracks") as cur:
            row = await cur.fetchone()
        return row["n"]

    async def delete_track(self, tid: int) -> None:
        await self.db.execute("DELETE FROM tracks WHERE id=?", (tid,))
        await self.db.commit()

    # ---------- 对话日志 ----------
    async def add_chat_log(
        self,
        did: str,
        query: str,
        answer: str,
        provider: str,
        latency_ms: int,
        status: str,
    ) -> None:
        await self.db.execute(
            "INSERT INTO chat_logs(did,ts,query,answer,provider,latency_ms,status) "
            "VALUES(?,?,?,?,?,?,?)",
            (did, _now(), query, answer, provider, latency_ms, status),
        )
        await self.db.commit()

    async def recent_chat_logs(self, did: str | None = None, limit: int = 50) -> list[dict]:
        if did:
            sql, args = "SELECT * FROM chat_logs WHERE did=? ORDER BY id DESC LIMIT ?", (did, limit)
        else:
            sql, args = "SELECT * FROM chat_logs ORDER BY id DESC LIMIT ?", (limit,)
        async with self.db.execute(sql, args) as cur:
            return [dict(r) async for r in cur]

    async def clear_chat_logs(self, did: str | None = None) -> None:
        if did:
            await self.db.execute("DELETE FROM chat_logs WHERE did=?", (did,))
        else:
            await self.db.execute("DELETE FROM chat_logs")
        await self.db.commit()

    # ---------- 同步日志 ----------
    async def add_sync_log(
        self, playlist_id: int, added: int, skipped: int, failed: int, message: str
    ) -> None:
        await self.db.execute(
            "INSERT INTO sync_logs(playlist_id,ts,added,skipped,failed,message) "
            "VALUES(?,?,?,?,?,?)",
            (playlist_id, _now(), added, skipped, failed, message),
        )
        await self.db.commit()

    async def recent_sync_logs(self, limit: int = 30) -> list[dict]:
        async with self.db.execute(
            "SELECT * FROM sync_logs ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [dict(r) async for r in cur]

    # ---------- Web 会话 ----------
    async def create_session(self, token: str, expires_at: str) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO sessions(token,created_at,expires_at) VALUES(?,?,?)",
            (token, _now(), expires_at),
        )
        await self.db.commit()

    async def get_session(self, token: str) -> dict | None:
        async with self.db.execute(
            "SELECT * FROM sessions WHERE token=?", (token,)
        ) as cur:
            return _row(await cur.fetchone())

    async def delete_session(self, token: str) -> None:
        await self.db.execute("DELETE FROM sessions WHERE token=?", (token,))
        await self.db.commit()

    async def purge_expired_sessions(self) -> None:
        await self.db.execute(
            "DELETE FROM sessions WHERE expires_at < ?", (_now(),)
        )
        await self.db.commit()
