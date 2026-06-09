"""Tests for Naver sise_time tick parser and minute conversion."""

from __future__ import annotations

from core.gap_naver_ticks import (
    datetime12_to_hhmmss,
    fetch_all_ticks_for_day,
    filter_regular_session_bars,
    in_regular_session,
    parse_fchart_minute_text,
    parse_sise_time_html,
    thistime_for_ymd,
    ticks_to_minute_bars,
    time_text_to_hhmmss,
)

_FCHART_SAMPLE = """
[['날짜', '시가', '고가', '저가', '종가', '거래량', '외국인소진율'],
["202606051528", null, null, null, 329000, 31299144, null],
["202606051529", null, null, null, 329500, 31299108, null],
["202606051530", null, null, null, 329000, 31298457, null],
["202606051558", null, null, null, 328000, 31299000, null],
["202606050838", null, null, null, 327000, 1000, null],
]
"""

_SAMPLE_HTML = """
<table cellspacing="0" class="type2">
<tr><th>체결시각</th><th>체결가</th><th>전일비</th><th>매도</th><th>매수</th><th>거래량</th><th>변동량</th></tr>
<tr>
<td align="center">10:30:15</td>
<td class="num">14,000</td>
<td class="num">상승 100</td>
<td class="num">&nbsp;</td>
<td class="num">&nbsp;</td>
<td class="num">500</td>
<td class="num">5</td>
</tr>
<tr>
<td align="center">10:29:00</td>
<td class="num">13,580</td>
<td class="num">하락 420</td>
<td class="num">&nbsp;</td>
<td class="num">&nbsp;</td>
<td class="num">1,200</td>
<td class="num">12</td>
</tr>
</table>
"""


def test_thistime_for_ymd():
    assert thistime_for_ymd("20250528") == "20250528180000"
    assert thistime_for_ymd("20250528", hhmmss="153000") == "20250528153000"


def test_time_text_to_hhmmss():
    assert time_text_to_hhmmss("10:30:15") == "103015"
    assert time_text_to_hhmmss("9:05:01") == "090501"


def test_parse_sise_time_html():
    ticks = parse_sise_time_html(_SAMPLE_HTML)
    assert len(ticks) == 2
    assert ticks[0].hhmmss == "103015"
    assert ticks[0].price == 14000
    assert ticks[1].price == 13580


def test_ticks_to_minute_bars():
    ticks = parse_sise_time_html(_SAMPLE_HTML)
    bars = ticks_to_minute_bars(ticks)
    assert len(bars) == 2
    assert bars[0]["hhmmss"] == "102900"
    assert bars[0]["low"] == 13580
    assert bars[1]["high"] == 14000


def test_datetime12_to_hhmmss():
    assert datetime12_to_hhmmss("202606051558") == "155800"


def test_parse_fchart_minute_text():
    bars = parse_fchart_minute_text(_FCHART_SAMPLE)
    assert len(bars) == 5
    assert bars[0]["hhmmss"] == "083800"
    assert bars[3]["hhmmss"] == "153000"
    assert bars[-1]["hhmmss"] == "155800"


def test_filter_regular_session_bars():
    assert in_regular_session("090000")
    assert in_regular_session("153000")
    assert not in_regular_session("083800")
    assert not in_regular_session("155800")
    raw = [
        {"hhmmss": "083800", "price": 1},
        {"hhmmss": "090000", "price": 2},
        {"hhmmss": "153000", "price": 3},
        {"hhmmss": "155800", "price": 4},
    ]
    filtered = filter_regular_session_bars(raw)
    assert [b["hhmmss"] for b in filtered] == ["090000", "153000"]


def test_fetch_all_ticks_for_day_no_network(monkeypatch):
    """Ensure pagination merge without real HTTP (monkeypatch)."""
    pages = {
        1: _SAMPLE_HTML,
        2: "",
    }

    class FakeResp:
        text = ""

        def raise_for_status(self):
            return None

    def fake_fetch(session, *, symbol, ymd, page, delay_sec=0.0):
        html = pages.get(page, "")
        from core.gap_naver_ticks import parse_sise_time_html
        return parse_sise_time_html(html)

    monkeypatch.setattr("core.gap_naver_ticks.fetch_sise_time_page", fake_fetch)
    ticks = fetch_all_ticks_for_day("005930", "20250528", delay_sec=0)
    assert len(ticks) == 2
    assert ticks[0].hhmmss == "102900"
