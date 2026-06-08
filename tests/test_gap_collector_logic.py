"""Tests for gap-up recovery strategy logic."""

from __future__ import annotations

from core.gap_collector_logic import (
    bar_volume_for_ymd,
    calc_trade_amounts,
    calc_volume_approx_fields,
    find_gap_candidate,
    gap_pct_from_ohlc,
    scan_gap_candidates_from_cache,
    simulate_gap_trade,
)


def _bars_for_gap(open_px: int, prev_close: int, *, close: int = 0):
    return [
        {"date": "2026.06.09", "open": open_px, "high": open_px + 500, "low": open_px - 500, "close": close or open_px},
        {"date": "2026.06.08", "open": prev_close, "high": prev_close, "low": prev_close, "close": prev_close},
    ]


def test_gap_pct_from_ohlc():
    assert gap_pct_from_ohlc(10300, 10000) == 3.0
    assert gap_pct_from_ohlc(10900, 10000) == 9.0


def test_find_gap_candidate_in_range():
    bars = _bars_for_gap(10500, 10000)
    cand = find_gap_candidate(bars, "20260609", gap_min_pct=3.0, gap_max_pct=9.0)
    assert cand is not None
    assert cand.open_price == 10500
    assert cand.gap_pct == 5.0


def test_find_gap_candidate_out_of_range():
    bars = _bars_for_gap(10200, 10000)
    assert find_gap_candidate(bars, "20260609") is None
    bars2 = _bars_for_gap(11000, 10000)
    assert find_gap_candidate(bars2, "20260609") is None


def test_scan_gap_candidates_from_cache():
    cache = {
        "005930": _bars_for_gap(10500, 10000),
        "000660": _bars_for_gap(10200, 10000),
    }
    found = scan_gap_candidates_from_cache(cache, "20260609")
    assert len(found) == 1
    assert found[0].symbol == "005930"


def test_simulate_gap_trade_trailing_stop():
    """open 14000, dip 3%, recover, rally 15000, trail -5% -> 14250."""
    open_px = 14000
    bars = [
        {"hhmmss": "090100", "price": 13800, "high": 13800, "low": 13580, "acml_tr_pbmn": 100},
        {"hhmmss": "100000", "price": 14000, "high": 14000, "low": 13900, "acml_tr_pbmn": 200},
        {"hhmmss": "110000", "price": 15000, "high": 15000, "low": 14900, "acml_tr_pbmn": 500},
        {"hhmmss": "120000", "price": 14200, "high": 14250, "low": 14200, "acml_tr_pbmn": 600},
    ]
    trade = simulate_gap_trade(open_px, bars, dip_min_pct=3.0, trailing_stop_pct=0.05)
    assert trade is not None
    assert trade.buy_price == 14000
    assert trade.buy_hhmmss == "100000"
    assert trade.max_dip_pct >= 3.0
    assert trade.sell_reason == "trailing"
    assert trade.sell_price == 14250


def test_simulate_gap_trade_close_exit():
    open_px = 10000
    bars = [
        {"hhmmss": "090100", "price": 9700, "high": 9700, "low": 9600, "acml_tr_pbmn": 50},
        {"hhmmss": "100000", "price": 10000, "high": 10050, "low": 9950, "acml_tr_pbmn": 100},
        {"hhmmss": "153000", "price": 10100, "high": 10100, "low": 10050, "acml_tr_pbmn": 200},
    ]
    trade = simulate_gap_trade(
        open_px,
        bars,
        dip_min_pct=3.0,
        trailing_stop_pct=0.05,
        close_price=10100,
    )
    assert trade is not None
    assert trade.sell_reason == "close"
    assert trade.sell_price == 10100


def test_simulate_no_trade_without_dip():
    open_px = 10000
    bars = [
        {"hhmmss": "100000", "price": 10050, "high": 10100, "low": 10000, "acml_tr_pbmn": 100},
    ]
    assert simulate_gap_trade(open_px, bars, dip_min_pct=3.0) is None


def test_calc_trade_amounts():
    amounts = calc_trade_amounts(
        10000,
        10500,
        1,
        fee_rate_buy=0.00015,
        fee_rate_sell=0.00015,
        tax_rate_sell=0.0018,
    )
    assert amounts["buy_amount"] == 10000
    assert amounts["sell_amount"] == 10500
    assert amounts["pnl"] < 500


def test_bar_volume_for_ymd():
    bars = [
        {"date": "2025.06.09", "open": 10500, "close": 10600, "volume": 12345},
        {"date": "2025.06.08", "open": 10000, "close": 10000, "volume": 9000},
    ]
    assert bar_volume_for_ymd(bars, "20250609") == 12345
    assert bar_volume_for_ymd(bars, "20250101") == 0


def test_calc_volume_approx_fields():
    fields = calc_volume_approx_fields(
        10000,
        500_000,
        market_cap_billion=1000,
        current_price=50000,
    )
    assert fields["daily_volume"] == 500_000
    assert fields["approx_trading_value_won"] == 5_000_000_000
    assert fields["approx_trading_value_billion"] == 50
    assert fields["shares_outstanding"] == 2_000_000
    assert fields["approx_market_cap_billion"] == 200
