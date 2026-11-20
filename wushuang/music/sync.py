"""歌单同步（多平台通用）：远端歌单 → 下载音频到 NAS 本地 → 入库为本地歌单。

选择"下载到本地"而非实时取链接：平台播放地址有有效期，本地化后
播放稳定、离线可用，也避免音箱播放时链接失效。已存在的曲目跳过。
平台差异全部收敛在 clients（统一接口），本引擎不感知具体平台。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import mutagen

from ..config import Settings
from ..crypto import SecretBox
from ..storage import Storage
from ..utils.text import sanitize_filename
from .platforms import PLATFORMS

_LOGGER = logging.getLogger(__name__)


class SyncError(Exception):
    pass


class PlaylistSync:
    def __init__(
        self,
        storage: Storage,
        box: SecretBox,
        settings: Settings,
        clients: dict,
    ):
        self.storage = storage
        self.box = box
        self.settings = settings
        self.clients = clients
        self.progress: dict[int, dict] = {}
        self._tasks: dict[int, asyncio.Task] = {}

    def start(self, playlist_id: int) -> None:
        """创建受跟踪的同步任务（关停时可取消，避免旧进程写库冲突）。"""
        task = asyncio.create_task(self.sync_playlist(playlist_id))
        self._tasks[playlist_id] = task
        task.add_done_callback(lambda t: self._tasks.pop(playlist_id, None))

    async def cancel_all(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    async def _load_cookie(self, source: str) -> bool:
        acc = await self.storage.get_music_account(source)
        if not acc:
            return False
        cookie = self.box.decrypt(acc.get("cookie_enc"))
        if not cookie:
            return False
        self.clients[source].set_cookie(cookie)
        return True

    async def sync_playlist(self, playlist_id: int) -> dict:
        pl = await self.storage.get_playlist(playlist_id)
        if not pl or pl["source"] not in self.clients or not pl["remote_id"]:
            return {"error": "不是可同步的远端歌单"}
        source = pl["source"]
        client = self.clients[source]
        meta = PLATFORMS[source]
        if playlist_id in self.progress and self.progress[playlist_id]["state"] == "running":
            return {"error": "该歌单正在同步中"}

        prog = {"state": "running", "total": 0, "done": 0,
                "added": 0, "skipped": 0, "failed": 0, "current": ""}
        self.progress[playlist_id] = prog
        folder = Path(pl["folder"]) if pl["folder"] else (
            self.settings.music_dir / source / sanitize_filename(pl["name"])
        )
        try:
            if not await self._load_cookie(source):
                raise SyncError(f"{meta.label}未登录，请先在后台扫码登录")
            songs = await client.playlist_tracks(pl["remote_id"])
            prog["total"] = len(songs)
            folder.mkdir(parents=True, exist_ok=True)
            level = await self.storage.get_setting(
                f"{source}_download_level", meta.default_level)
            concurrency = max(1, int(await self.storage.get_setting(
                f"{source}_sync_concurrency", "3")))

            # 预筛：已存在的跳过；其余批量取播放地址
            pending: list[dict] = []
            for song in songs:
                sid = str(song.get("id") or "")
                existing = await self.storage.track_by_remote_id(source, sid)
                if existing and Path(existing["path"]).is_file():
                    prog["skipped"] += 1
                    prog["done"] += 1
                else:
                    pending.append(song)
            url_map: dict[str, dict] = {}
            ids = [str(s.get("id") or "") for s in pending]
            for i in range(0, len(ids), 100):
                url_map.update(await client.song_urls(ids[i:i + 100], level))

            sem = asyncio.Semaphore(concurrency)

            async def _one(song: dict) -> None:
                sid = str(song.get("id") or "")
                name = song.get("name") or "未知歌曲"
                artists = song.get("artist") or ""
                album = song.get("album") or ""
                label = f"{artists} - {name}"
                async with sem:
                    prog["current"] = label
                    try:
                        info = url_map.get(sid) or {}
                        url = info.get("url")
                        if not url:
                            prog["failed"] += 1
                            _LOGGER.info("无播放地址（版权/权益）: %s", label)
                            return
                        ext = info.get("type") or "mp3"
                        filename = sanitize_filename(f"{artists} - {name}") + f".{ext}"
                        dest = folder / filename
                        await self._download(url, dest)
                        await asyncio.to_thread(self._tag, dest, name, artists, album)
                        await self.storage.upsert_track(
                            title=name, path=str(dest), artist=artists, album=album,
                            playlist_id=playlist_id, remote_id=sid, source=source,
                        )
                        prog["added"] += 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001 - 单曲失败不中断整体
                        prog["failed"] += 1
                        _LOGGER.warning("同步单曲失败 %s: %s", label, e)
                    finally:
                        prog["done"] += 1

            await asyncio.gather(*[_one(s) for s in pending])

            status = (
                f"完成：新增 {prog['added']}，跳过 {prog['skipped']}，失败 {prog['failed']}"
            )
            prog["state"] = "done"
            await self.storage.update_playlist(
                playlist_id, last_status=status,
                last_sync_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                folder=str(folder),
            )
            await self.storage.add_sync_log(
                playlist_id, prog["added"], prog["skipped"], prog["failed"], status
            )
            return prog
        except Exception as e:  # noqa: BLE001
            prog["state"] = "error"
            prog["error"] = str(e)
            await self.storage.update_playlist(playlist_id, last_status=f"失败：{e}")
            await self.storage.add_sync_log(playlist_id, 0, 0, 0, f"失败：{e}")
            _LOGGER.warning("歌单同步失败 id=%s: %s", playlist_id, e)
            return prog

    async def _download(self, url: str, dest: Path) -> None:
        tmp = dest.with_suffix(dest.suffix + ".part")
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise SyncError(f"下载 HTTP {resp.status_code}")
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes(256 * 1024):
                        f.write(chunk)
        tmp.replace(dest)

    @staticmethod
    def _tag(path: Path, title: str, artist: str, album: str) -> None:
        try:
            audio = mutagen.File(path, easy=True)
            if audio is not None:
                audio["title"] = title
                audio["artist"] = artist
                if album:
                    audio["album"] = album
                audio.save()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("写标签失败: %s", path, exc_info=True)
