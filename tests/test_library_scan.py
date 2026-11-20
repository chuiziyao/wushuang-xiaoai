"""曲库扫描测试：跳过群晖系统目录。"""

from pathlib import Path

from wushuang.music.library import _scan_sync


def test_scan_skips_synology_dirs(tmp_path):
    (tmp_path / "album").mkdir()
    (tmp_path / "album" / "song.mp3").write_bytes(b"fake")
    (tmp_path / "#recycle").mkdir()
    (tmp_path / "#recycle" / "deleted.mp3").write_bytes(b"fake")
    (tmp_path / "@eaDir").mkdir()
    (tmp_path / "@eaDir" / "thumb.mp3").write_bytes(b"fake")
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.flac").write_bytes(b"fake")
    records = _scan_sync(tmp_path)
    names = [Path(r["path"]).name for r in records]
    assert names == ["song.mp3"]
