"""桌面版单实例 IPC 测试：第二次双击通知置前内置窗口（不调用系统浏览器）。"""

import socket
import threading

from wushuang import desktop


class FakeWindow:
    def __init__(self):
        self.calls = []
        self._on_top = False

    def restore(self):
        self.calls.append("restore")

    def show(self):
        self.calls.append("show")

    @property
    def on_top(self):
        return self._on_top

    @on_top.setter
    def on_top(self, v):
        self._on_top = v
        self.calls.append(("on_top", v))


def _start_server(holder):
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lock.bind(("127.0.0.1", desktop._LOCK_PORT))
    lock.listen(5)
    t = threading.Thread(target=desktop._serve_lock_commands,
                         args=(lock, holder), daemon=True)
    t.start()
    return lock


def test_netfx_config_enables_remote_sources():
    """0x80131515 根治开关：exe.config 必须含 loadFromRemoteSources。"""
    assert "loadFromRemoteSources" in desktop._NETFX_CONFIG
    assert 'enabled="true"' in desktop._NETFX_CONFIG


def test_notify_no_instance():
    assert desktop._notify_existing_instance() == ""


def test_show_command_focuses_window():
    win = FakeWindow()
    lock = _start_server({"win": win})
    try:
        assert desktop._notify_existing_instance() == "ok"
        assert win.calls == ["restore", "show", ("on_top", True), ("on_top", False)]
    finally:
        lock.close()


def test_show_command_no_window():
    lock = _start_server({})
    try:
        assert desktop._notify_existing_instance() == "no-window"
    finally:
        lock.close()


def test_focus_window_none():
    assert desktop._focus_window(None) is False


def test_focus_window_swallows_errors():
    class Broken:
        def restore(self):
            raise RuntimeError("窗口已销毁")

    assert desktop._focus_window(Broken()) is False


def test_acquire_single_instance_conflict():
    lock = desktop._acquire_single_instance()
    assert lock is not None
    try:
        assert desktop._acquire_single_instance() is None
    finally:
        lock.close()
    # 释放后可再次获取
    lock2 = desktop._acquire_single_instance()
    assert lock2 is not None
    lock2.close()
