"""时区显示测试：存储 UTC → 页面按 WUSHUANG_TZ（默认 Asia/Shanghai）。"""

from wushuang.utils.timefmt import display_tz, fmt_ts, parse_ts


def test_utc_to_shanghai(monkeypatch):
    monkeypatch.delenv("WUSHUANG_TZ", raising=False)
    # 截图场景：库里 15:19 UTC，页面应显示 23:19 北京时间
    assert fmt_ts("2026-11-20T15:19:00+00:00") == "2026-11-20 23:19:00"
    assert fmt_ts("2026-11-20T15:19:00+00:00", "%H:%M:%S") == "23:19:00"
    assert fmt_ts("2026-11-20T15:19:00+00:00", "%Y-%m-%d %H:%M") == "2026-11-20 23:19"


def test_naive_legacy_ts_treated_as_utc(monkeypatch):
    monkeypatch.delenv("WUSHUANG_TZ", raising=False)
    assert fmt_ts("2026-11-20T15:19:00") == "2026-11-20 23:19:00"


def test_empty_and_invalid(monkeypatch):
    monkeypatch.delenv("WUSHUANG_TZ", raising=False)
    assert fmt_ts(None) == "—"
    assert fmt_ts("") == "—"
    assert fmt_ts("not-a-date") == "—"
    assert parse_ts(None) is None


def test_custom_tz(monkeypatch):
    monkeypatch.setenv("WUSHUANG_TZ", "UTC")
    assert fmt_ts("2026-11-20T15:19:00+00:00", "%H:%M:%S") == "15:19:00"
    monkeypatch.setenv("WUSHUANG_TZ", "Asia/Tokyo")
    assert fmt_ts("2026-11-20T15:19:00+00:00", "%H:%M:%S") == "00:19:00"


def test_bad_tz_falls_back(monkeypatch):
    monkeypatch.setenv("WUSHUANG_TZ", "Not/AZone")
    assert str(display_tz()) == "Asia/Shanghai"
    assert fmt_ts("2026-11-20T15:19:00+00:00", "%H:%M:%S") == "23:19:00"


def test_jinja_filter_registered():
    from wushuang.web.pages import templates

    assert templates.env.filters["localtime"] is fmt_ts
    out = templates.env.from_string("{{ ts|localtime('%H:%M:%S') }}").render(
        ts="2026-11-20T15:19:00+00:00"
    )
    assert out == "23:19:00"
