"""Tests for Naver sise_time tick parser and minute conversion."""

from __future__ import annotations

from core.gap_naver_ticks import (
    fetch_all_ticks_for_day,
    parse_sise_time_html,
    thistime_for_ymd,
    ticks_to_minute_bars,
    time_text_to_hhmmss,
)

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
