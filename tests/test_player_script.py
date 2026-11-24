"""播放页内联脚本语法回归：有 node 时做 --check，防止重复声明等 SyntaxError
导致整段 JS 失效（下拉/歌单/曲库全空的历史 bug）。"""

import re
import shutil
import subprocess

import pytest
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "wushuang" / "web" / "templates"


def test_player_inline_script_syntax(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node 不可用，跳过 JS 语法检查")
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)
    from wushuang.utils.timefmt import fmt_ts
    env.filters["localtime"] = fmt_ts
    for tpl in ("player.html", "music.html"):
        html = env.get_template(tpl).render(
            app_name="x", version="0", active="player",
            devices=[{"did": "d1", "name": "Pro", "hardware": "LX06",
                      "enabled": 1, "config_json": "{}", "updated_at": "x"}],
            playlists=[], tracks=0, syncs=[], music_dir="/music",
            music_platforms=[], netease={},
        )
        scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
        assert scripts, f"{tpl} 应含内联脚本"
        for i, s in enumerate(scripts):
            f = tmp_path / f"{tpl}-{i}.js"
            f.write_text(s, encoding="utf-8")
            r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
            assert r.returncode == 0, f"{tpl} 内联脚本语法错误:\n{r.stderr}"
