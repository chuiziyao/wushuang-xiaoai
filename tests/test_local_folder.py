"""本地歌单目录映射与显示测试。"""

from pathlib import Path

import pytest

from wushuang.music.library import display_folder, resolve_local_folder


def test_resolve_empty_creates_local_subdir_base(tmp_path):
    assert resolve_local_folder(tmp_path, "", "") == tmp_path / "local"


def test_resolve_host_path_maps_to_container(tmp_path):
    got = resolve_local_folder(tmp_path, "/volume1/Music", "/volume1/Music/DJ合集")
    assert got == tmp_path / "DJ合集"


def test_resolve_container_path_accepted(tmp_path):
    got = resolve_local_folder(tmp_path, "/volume1/Music", str(tmp_path / "已有"))
    assert got == tmp_path / "已有"


def test_resolve_outside_rejected(tmp_path):
    with pytest.raises(ValueError):
        resolve_local_folder(tmp_path, "/volume1/Music", "/etc")
    with pytest.raises(ValueError):
        resolve_local_folder(tmp_path, "", "relative/dir")


def test_display_folder_maps_back(tmp_path):
    assert display_folder(tmp_path, "/volume1/Music", str(tmp_path / "netease/666")) == \
        "/volume1/Music/netease/666"
    assert display_folder(tmp_path, "", str(tmp_path / "x")) == str(tmp_path / "x")
