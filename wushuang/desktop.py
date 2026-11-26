"""桌面版启动器：Windows / macOS / Linux 单文件程序入口。

架构：同一套 FastAPI 服务在后台线程运行（音箱仍通过局域网 HTTP 拉流），
前台用 pywebview **内置浏览器窗口**（Windows=WebView2 / macOS=WKWebView /
Linux=WebKitGTK）打开管理后台。**全程不主动调用系统浏览器**：
- 外部链接也在内置窗口内打开（避免系统浏览器的提权/完整性弹窗）
- 第二次双击通过本地 IPC 通知已有实例把内置窗口置前
- 内置窗口确实不可用时仅弹窗告知管理地址，由用户自行决定如何打开

与 Docker 版的差异（WUSHUANG_DESKTOP=1）：
- 不接入音乐平台 sidecar（无网易云/酷狗/QQ/汽水登录与同步）
- 本地曲库扫描包含视频文件；支持在线音源直链
- 大模型对话、小米扫码、设备管理、播放控制、语音指令完全一致

数据目录：
- Windows  %APPDATA%/WushuangXiaoai
- macOS    ~/Library/Application Support/WushuangXiaoai
- Linux    ~/.local/share/wushuang-xiaoai
媒体目录默认 ~/Music/WushuangXiaoai，可在后台「设置」页修改（重启生效）。
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
from pathlib import Path

from . import APP_NAME, __version__

_LOGGER = logging.getLogger("wushuang.desktop")

_LOCK_PORT = 47653  # 单实例锁（仅绑定 127.0.0.1）


def _app_dir() -> Path:
    """打包后=exe 所在目录；源码运行=项目根。便携模式数据随程序目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_data_dir() -> Path:
    # 打包版优先便携：数据放在程序目录内，避免系统 TEMP/用户配置目录的
    # 权限或安全软件拦截（onefile 解压临时目录失败的同类问题）。
    if getattr(sys, "frozen", False):
        return _app_dir() / "data"
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
        return Path(base) / "WushuangXiaoai"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/WushuangXiaoai"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(base) / "wushuang-xiaoai"


def default_music_dir() -> Path:
    if getattr(sys, "frozen", False):
        return _app_dir() / "media"
    return Path.home() / "Music" / "WushuangXiaoai"


def _writable_dir(candidates: list[Path]) -> Path:
    """返回第一个可创建且可写的目录；全失败抛错。"""
    last_err: Exception | None = None
    for c in candidates:
        if not str(c):
            continue
        try:
            c.mkdir(parents=True, exist_ok=True)
            probe = c / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return c
        except OSError as e:
            last_err = e
            continue
    raise RuntimeError(f"找不到可写的数据目录: {last_err}")


def load_desktop_config(data_dir: Path) -> dict:
    try:
        return json.loads((data_dir / "desktop.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def save_desktop_config(data_dir: Path, cfg: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "desktop.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


def schedule_shutdown(delay: float = 0.5) -> None:
    """从 Web UI 可靠退出整个桌面进程（托盘不可用时的退出入口）。"""
    threading.Timer(delay, lambda: os._exit(0)).start()


def schedule_restart(delay: float = 0.6) -> None:
    """延迟重启进程（先让 HTTP 响应返回）。单文件 exe 与源码运行均支持。"""
    def _restart():
        if getattr(sys, "frozen", False):
            os.execv(sys.executable, [sys.executable] + sys.argv[1:])
        else:
            os.execv(sys.executable,
                     [sys.executable, "-m", "wushuang.desktop"] + sys.argv[1:])
    threading.Timer(delay, _restart).start()


def _pick_port(preferred: int) -> int:
    for port in [preferred] + [p for p in range(8080, 8091) if p != preferred]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("找不到可用端口（8080–8090 均被占用）")


# .NET Framework 默认拒绝从"网络位置"加载程序集（HRESULT 0x80131515）：
# 某些机器把非系统盘（如 D:）或从网上下载解压的文件（Mark of the Web）判为
# 网络区域，导致 pythonnet 的 Python.Runtime.dll 加载失败、内置 WebView2
# 窗口起不来。loadFromRemoteSources 开关显式允许加载本程序自带的程序集。
_NETFX_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <runtime>
    <loadFromRemoteSources enabled="true"/>
  </runtime>
</configuration>
"""


def _ensure_netfx_config() -> None:
    """打包版 Windows：确保 exe 旁有 <exe>.exe.config（CLR 启动时读取）。"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    cfg = Path(sys.executable).with_suffix(".exe.config")
    try:
        if not cfg.exists():
            cfg.write_text(_NETFX_CONFIG, encoding="utf-8")
            _LOGGER.info("已写入 %s（允许加载本地程序集）", cfg.name)
    except OSError:
        _LOGGER.warning("无法写入 %s，内置窗口可能受限", cfg, exc_info=True)


def _setup_logging(data_dir: Path) -> None:
    log_dir = data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            log_dir / "desktop.log", maxBytes=2_000_000, backupCount=3,
            encoding="utf-8"),
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def _icon_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "icon.png"
    return Path(__file__).resolve().parent.parent / "assets" / "icon.png"


def _message_box(text: str, title: str) -> None:
    """原生 MessageBox 告知管理地址（不调用任何浏览器）。"""
    if sys.platform != "win32":
        return
    if os.environ.get("WUSHUANG_NO_DIALOG"):  # 无人值守/自动化测试抑制弹窗
        return
    try:
        import ctypes
        # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40 | 0x10000 | 0x40000)
    except Exception:  # noqa: BLE001
        pass


def _show_url_message(url: str, extra: str = "") -> None:
    """内置窗口不可用时的兜底：只告知地址，绝不自动打开系统浏览器。"""
    _message_box(
        f"{APP_NAME} 桌面版已在后台运行。\n\n"
        f"管理地址：{url}\n"
        f"初始管理密码：wushuang（登录后请在设置中修改）。\n\n"
        f"{extra}"
        f"程序不会自动调用系统浏览器，可复制链接自行打开。\n"
        f"关闭本提示不影响服务；再次双击程序会尝试置前内置窗口。",
        f"{APP_NAME} 桌面版",
    )


_WEBVIEW2_HINT = (
    "内置窗口启动失败（详见 data/logs/desktop.log）。\n"
    "若系统缺少 WebView2 运行时，请安装微软官方组件：\n"
    "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
)


def _focus_window(win) -> bool:
    """把内置窗口恢复并带到前台（供第二次双击/托盘调用）。"""
    if win is None:
        return False
    try:
        win.restore()
        win.show()
        win.on_top = True   # pywebview 的 on_top 是属性：短暂置顶抢焦点
        win.on_top = False
        return True
    except Exception:  # noqa: BLE001 - 窗口可能已销毁
        _LOGGER.warning("置前内置窗口失败", exc_info=True)
        return False


def _notify_existing_instance() -> str:
    """第二次双击：通过锁端口 IPC 通知已有实例置前窗口。返回应答。"""
    try:
        with socket.create_connection(("127.0.0.1", _LOCK_PORT), timeout=3) as s:
            s.sendall(b"show\n")
            s.settimeout(3)
            return (s.recv(64) or b"").decode("ascii", "replace").strip()
    except OSError:
        return ""


def _serve_lock_commands(lock: socket.socket, holder: dict) -> None:
    """已有实例侧：接受 'show' 命令，把内置窗口带到前台。"""
    while True:
        try:
            conn, _ = lock.accept()
        except OSError:
            return  # 锁关闭（进程退出）
        try:
            conn.settimeout(3)
            req = (conn.recv(64) or b"").strip()
            if req == b"show":
                ok = _focus_window(holder.get("win"))
                conn.sendall(b"ok" if ok else b"no-window")
        except OSError:
            pass
        finally:
            conn.close()


def _tray_loop(url: str, holder: dict) -> None:
    """无内置窗口时的托盘图标：置前窗口/告知地址 / 退出（阻塞直到退出）。"""
    import pystray
    from PIL import Image

    img = Image.open(_icon_path())

    def on_open(icon, item):
        if not _focus_window(holder.get("win")):
            _show_url_message(url)

    def on_quit(icon, item):
        icon.stop()

    icon = pystray.Icon(
        "wushuang-xiaoai", img, f"{APP_NAME} 桌面版",
        menu=pystray.Menu(
            pystray.MenuItem("打开管理后台", on_open, default=True),
            pystray.MenuItem("退出", on_quit),
        ),
    )
    icon.run()


def _acquire_single_instance() -> socket.socket | None:
    """返回持有的锁 socket；端口被占（已有实例）返回 None。"""
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", _LOCK_PORT))
        lock.listen(5)
        return lock
    except OSError:
        lock.close()
        return None


def main() -> None:
    override = os.environ.get("WUSHUANG_DATA_DIR")
    candidates = [Path(override)] if override else [default_data_dir()]
    # 回退链：程序目录 → LOCALAPPDATA → APPDATA（兼容装在只读位置等情况）
    candidates += [
        _app_dir() / "data",
        Path(os.environ.get("LOCALAPPDATA", "")) / "WushuangXiaoai",
        Path(os.environ.get("APPDATA", "")) / "WushuangXiaoai",
    ]
    data_dir = _writable_dir(candidates)
    _setup_logging(data_dir)
    _ensure_netfx_config()
    cfg = load_desktop_config(data_dir)

    music_dir = Path(cfg.get("music_dir") or default_music_dir()).expanduser()
    try:
        music_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        _LOGGER.warning("媒体目录不可用，改用程序目录内 media: %s", music_dir)
        music_dir = data_dir / "media"
        music_dir.mkdir(parents=True, exist_ok=True)

    lock = _acquire_single_instance()
    if lock is None:
        # 第二次双击：通知已有实例把内置窗口置前（不调用系统浏览器）
        ack = _notify_existing_instance()
        if ack in ("ok", "no-window"):
            _LOGGER.info("已有实例在运行（%s），退出", ack)
            return
        try:
            url = (data_dir / "instance.url").read_text("utf-8").strip()
            if url:
                _show_url_message(url, "已有实例正在运行或启动中。\n")
        except OSError:
            pass
        return

    port = _pick_port(int(cfg.get("port") or 8080))
    os.environ["WUSHUANG_DESKTOP"] = "1"
    os.environ["WUSHUANG_DATA_DIR"] = str(data_dir)
    os.environ["WUSHUANG_MUSIC_DIR"] = str(music_dir)
    os.environ["WUSHUANG_PORT"] = str(port)
    os.environ["WUSHUANG_HOST"] = "0.0.0.0"
    if cfg.get("public_url"):
        os.environ["WUSHUANG_PUBLIC_URL"] = cfg["public_url"]
    os.environ.setdefault("WUSHUANG_ADMIN_PASSWORD", "wushuang")

    url = f"http://127.0.0.1:{port}"
    (data_dir / "instance.url").write_text(url, "utf-8")
    _LOGGER.info("%s 桌面版 v%s 启动：数据 %s，媒体 %s，端口 %s",
                 APP_NAME, __version__, data_dir, music_dir, port)

    import uvicorn

    server = uvicorn.Server(uvicorn.Config(
        "wushuang.app:app", host="0.0.0.0", port=port,
        log_level="info", log_config=None, lifespan="on",
    ))
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        if not thread.is_alive():
            _LOGGER.error("后台服务启动失败，详见日志 %s", data_dir / "logs")
            return
        time.sleep(0.1)
    if not server.started:
        _LOGGER.error("后台服务启动超时")
        server.should_exit = True
        return

    browser_mode = "--browser" in sys.argv
    win_holder: dict = {}
    threading.Thread(target=_serve_lock_commands, args=(lock, win_holder),
                     name="instance-ipc", daemon=True).start()
    try:
        if browser_mode:
            raise ImportError("browser mode requested")
        import webview

        # 外部链接（target=_blank 等）也在内置窗口打开，不递送系统浏览器，
        # 避免系统浏览器的提权/完整性确认弹窗
        webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
        win = webview.create_window(
            f"{APP_NAME} 桌面版 v{__version__}", url,
            width=1200, height=820, min_size=(960, 640),
        )
        win_holder["win"] = win
        # private_mode=False：持久化 Cookie，登录态跨启动保留
        webview.start(private_mode=False, storage_path=str(data_dir / "webview"))
    except Exception as e:  # noqa: BLE001 - 内置窗口不可用：服务继续，仅告知地址
        if not browser_mode:
            _LOGGER.warning("内置窗口不可用（%s），服务继续运行", e, exc_info=True)
            _show_url_message(url, _WEBVIEW2_HINT)
        else:
            _LOGGER.info("--browser 模式：不创建窗口，地址 %s", url)
        print(f"{APP_NAME} 桌面版已在后台运行：{url}")
        try:
            _tray_loop(url, win_holder)  # 托盘「退出」结束循环
        except Exception as tray_err:  # noqa: BLE001 - 无托盘环境退化为常驻
            _LOGGER.warning("托盘不可用（%s），服务常驻；再次双击可置前窗口，结束请用任务管理器",
                            tray_err)
            try:
                while thread.is_alive():
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        lock.close()
        _LOGGER.info("桌面版已退出")


if __name__ == "__main__":
    main()