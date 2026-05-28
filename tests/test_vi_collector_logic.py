"""Tests for VI collector pure logic."""

from __future__ import annotations

from core.vi_collector_logic import (
    build_vi_event_row,
    has_second_static_upward_vi,
    market_cap_group,
    post_release_pct_range,
    pre_vi_trading_value,
    select_static_upward_first_vi,
)


def _static_up(sym: str, hour: str, vi_count: int = 1, price: int = 11000) -> dict:
    return {
        "mksc_shrn_iscd": sym,
        "cntg_vi_hour": hour,
        "vi_cncl_hour": f"{int(hour[:2])+1:02d}{hour[2:]}",
        "vi_prc": price,
        "vi_stnd_prc": 10000,
        "vi_kind_code": "1",
        "vi_count": vi_count,
    }


def _dynamic(sym: str, hour: str = "093000") -> dict:
    return {
        "mksc_shrn_iscd": sym,
        "cntg_vi_hour": hour,
        "vi_kind_code": "2",
        "vi_dmc_stnd_prc": 10000,
        "vi_stnd_prc": 0,
    }


def test_market_cap_groups():
    assert market_cap_group(500) == "A"
    assert market_cap_group(800) == "A"
    assert market_cap_group(801) == "B"
    assert market_cap_group(2000) == "B"
    assert market_cap_group(2001) == "C"
    assert market_cap_group(100001) == "G"


def test_exclude_dynamic_before_static():
    static = [_static_up("005930", "103000")]
    dynamic = [_dynamic("005930", "101000")]
    assert select_static_upward_first_vi(static, dynamic) == []

    dynamic_other = [_dynamic("000660", "101000")]
    selected = select_static_upward_first_vi(static, dynamic_other)
    assert len(selected) == 1
    assert selected[0]["mksc_shrn_iscd"] == "005930"


def test_select_first_static_upward_only():
    static = [
        _static_up("005930", "110000", vi_count=2),
        _static_up("005930", "103000", vi_count=1),
        _static_up("035720", "104500", vi_count=1),
    ]
    selected = select_static_upward_first_vi(static, [])
    assert len(selected) == 2
    by_sym = {r["mksc_shrn_iscd"]: r for r in selected}
    assert by_sym["005930"]["vi_count"] == 1
    assert by_sym["005930"]["cntg_vi_hour"] == "103000"


def test_second_vi_flag():
    static = [
        _static_up("005930", "103000", vi_count=1),
        _static_up("005930", "113000", vi_count=2),
    ]
    assert has_second_static_upward_vi("005930", static) is True
    assert has_second_static_upward_vi("035720", static) is False


def test_pre_vi_trading_value_and_post_release_pct():
    bars = [
        {"hhmmss": "103000", "price": 8500, "high": 8500, "low": 8400, "acml_tr_pbmn": 1234567890},
        {"hhmmss": "103200", "price": 8600, "high": 8700, "low": 8550, "acml_tr_pbmn": 1300000000},
        {"hhmmss": "150000", "price": 8200, "high": 8250, "low": 8100, "acml_tr_pbmn": 2000000000},
    ]
    assert pre_vi_trading_value(bars, "103010") == 1234567890
    high_pct, low_pct = post_release_pct_range(bars, "103200", 8500)
    assert high_pct == round((8700 / 8500 - 1) * 100, 1)
    assert low_pct == round((8100 / 8500 - 1) * 100, 1)


def test_build_vi_event_row():
    raw = _static_up("417010", "093410", price=15250)
    raw["vi_cncl_hour"] = "093610"
    raw["hts_kor_isnm"] = "TestCo"
    bars = [
        {"hhmmss": "093400", "price": 15200, "high": 15250, "low": 15100, "acml_tr_pbmn": 500000000},
        {"hhmmss": "093600", "price": 15250, "high": 15250, "low": 15250, "acml_tr_pbmn": 520000000},
        {"hhmmss": "100000", "price": 15800, "high": 15800, "low": 15300, "acml_tr_pbmn": 800000000},
        {"hhmmss": "120000", "price": 14500, "high": 14600, "low": 14400, "acml_tr_pbmn": 900000000},
    ]
    event = build_vi_event_row(
        raw,
        static_up_rows=[raw],
        minute_bars=bars,
        market_cap_billion=1500,
        market="KOSDAQ",
        name="TestCo",
    )
    assert event.symbol == "417010"
    assert event.release_price == 15250
    assert event.pre_vi_trading_value == 500000000
    assert event.cap_group == "B"
    assert event.has_second_vi is False
