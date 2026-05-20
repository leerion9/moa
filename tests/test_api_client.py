from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.api_client import KISApiClient, Quote, SymbolHistory, _safe_abs_int


# ---------------------------------------------------------------------------
# _safe_abs_int
# ---------------------------------------------------------------------------

def test_safe_abs_int_normal():
    assert _safe_abs_int(12345) == 12345
    assert _safe_abs_int(-500) == 500
    assert _safe_abs_int("1,234,567") == 1234567
    assert _safe_abs_int(None) == 0
    assert _safe_abs_int("") == 0
    assert _safe_abs_int("abc") == 0


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------

def test_quote_w52_fields_default_zero():
    q = Quote(
        symbol="005930",
        current_price=70000,
        open_price=69000,
        volume=500000,
        prev_high=71000,
        prev_low=68000,
    )
    assert q.w52_high == 0
    assert q.w52_low == 0


def test_quote_w52_fields_set():
    q = Quote(
        symbol="005930",
        current_price=70000,
        open_price=69000,
        volume=500000,
        prev_high=71000,
        prev_low=68000,
        w52_high=85000,
        w52_low=55000,
    )
    assert q.w52_high == 85000
    assert q.w52_low == 55000


# ---------------------------------------------------------------------------
# is_open_trading_day
# ---------------------------------------------------------------------------

def test_is_open_trading_day_true_false_none():
    client = KISApiClient(settings=SimpleNamespace())

    client.get_holiday_info = lambda base_date_yyyymmdd: [{"opnd_yn": "Y"}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260327") is True

    client.get_holiday_info = lambda base_date_yyyymmdd: [{"opnd_yn": "N"}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260328") is False

    client.get_holiday_info = lambda base_date_yyyymmdd: [{"opnd_yn": ""}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260329") is None


def test_is_open_trading_day_opnd_yn_uppercase_key():
    client = KISApiClient(settings=SimpleNamespace())
    client.get_holiday_info = lambda base_date_yyyymmdd: [{"OPND_YN": "N"}]  # type: ignore[method-assign]
    assert client.is_open_trading_day("20260101") is False


# ---------------------------------------------------------------------------
# get_symbol_history
# ---------------------------------------------------------------------------

def _make_settings():
    return SimpleNamespace(
        base_url="https://fake",
        app_key="k",
        app_secret="s",
        kis_min_request_interval_sec=0.0,
        kis_api_retry_max=1,
        kis_rate_limit_retry_sleep_sec=0.0,
        request_timeout_sec=5,
        is_paper_trading=True,
    )


def _make_ohlcv_row(date: str, close: int = 10000) -> dict:
    return {
        "stck_bsop_date": date,
        "stck_oprc": str(close - 100),
        "stck_hgpr": str(close + 200),
        "stck_lwpr": str(close - 300),
        "stck_clpr": str(close),
        "acml_vol": "100000",
    }


def test_get_symbol_history_single_page():
    """단일 페이지 응답 파싱 확인."""
    client = KISApiClient(settings=_make_settings())

    fake_response = {
        "rt_cd": "0",
        "output1": {"w52_hgpr": "85000", "w52_lwpr": "55000"},
        "output2": [
            _make_ohlcv_row("20260519", 70000),
            _make_ohlcv_row("20260518", 69500),
            _make_ohlcv_row("20260515", 69000),
        ],
    }

    with patch.object(client, "_request_get_json", return_value=fake_response):
        hist = client.get_symbol_history("005930", days=5)

    assert isinstance(hist, SymbolHistory)
    assert hist.symbol == "005930"
    assert hist.w52_high == 85000
    assert hist.w52_low == 55000
    assert len(hist.bars) == 3
    # 최신순 정렬 확인
    assert hist.bars[0]["date"] == "20260519"
    assert hist.bars[1]["date"] == "20260518"
    assert hist.bars[2]["date"] == "20260515"


def test_get_symbol_history_bar_fields():
    """bars 각 필드가 올바르게 파싱되는지 확인."""
    client = KISApiClient(settings=_make_settings())

    fake_response = {
        "rt_cd": "0",
        "output1": {"w52_hgpr": "90000", "w52_lwpr": "50000"},
        "output2": [
            {
                "stck_bsop_date": "20260519",
                "stck_oprc": "69000",
                "stck_hgpr": "71500",
                "stck_lwpr": "68000",
                "stck_clpr": "70000",
                "acml_vol": "1234567",
            }
        ],
    }

    with patch.object(client, "_request_get_json", return_value=fake_response):
        hist = client.get_symbol_history("005930", days=10)

    bar = hist.bars[0]
    assert bar["date"] == "20260519"
    assert bar["open"] == 69000
    assert bar["high"] == 71500
    assert bar["low"] == 68000
    assert bar["close"] == 70000
    assert bar["volume"] == 1234567


def test_get_symbol_history_dedup_newest_first():
    """중복 날짜 제거 + 최신순 정렬 확인."""
    client = KISApiClient(settings=_make_settings())

    fake_response = {
        "rt_cd": "0",
        "output1": {},
        "output2": [
            _make_ohlcv_row("20260515"),
            _make_ohlcv_row("20260519"),
            _make_ohlcv_row("20260515"),  # 중복
            _make_ohlcv_row("20260518"),
        ],
    }

    with patch.object(client, "_request_get_json", return_value=fake_response):
        hist = client.get_symbol_history("005930", days=10)

    dates = [b["date"] for b in hist.bars]
    assert len(dates) == 3  # 중복 제거
    assert dates == sorted(dates, reverse=True)  # 최신순


def test_get_symbol_history_days_limit():
    """days 파라미터로 반환 개수 제한 확인."""
    client = KISApiClient(settings=_make_settings())

    bars = [_make_ohlcv_row(f"20260{500 - i:03d}"[:8] if False else f"2026051{i % 10}") for i in range(1, 8)]
    # 날짜 직접 생성
    date_rows = [
        _make_ohlcv_row(f"202605{10 + i:02d}") for i in range(10)
    ]
    fake_response = {
        "rt_cd": "0",
        "output1": {"w52_hgpr": "70000", "w52_lwpr": "50000"},
        "output2": date_rows,
    }

    with patch.object(client, "_request_get_json", return_value=fake_response):
        hist = client.get_symbol_history("005930", days=5)

    assert len(hist.bars) == 5


def test_get_symbol_history_empty_output():
    """output2 빈 응답 시 bars=[] 반환."""
    client = KISApiClient(settings=_make_settings())

    fake_response = {"rt_cd": "0", "output1": {}, "output2": []}

    with patch.object(client, "_request_get_json", return_value=fake_response):
        hist = client.get_symbol_history("000000", days=10)

    assert hist.bars == []
    assert hist.w52_high == 0
    assert hist.w52_low == 0


# ---------------------------------------------------------------------------
# get_market_cap_list
# ---------------------------------------------------------------------------

def _make_cap_row(symbol: str, cap: int) -> dict:
    return {"mksc_shrn_iscd": symbol, "stck_avls": str(cap)}


def test_get_market_cap_list_sorted_descending():
    """시총 내림차순 정렬 확인."""
    client = KISApiClient(settings=_make_settings())

    kospi_rows = [_make_cap_row("005930", 4000000), _make_cap_row("000660", 800000)]
    kosdaq_rows = [_make_cap_row("035720", 500000), _make_cap_row("263750", 100000)]

    call_count = 0

    def fake_get_rows(fid_input_iscd: str):
        nonlocal call_count
        call_count += 1
        return kospi_rows if fid_input_iscd == "0001" else kosdaq_rows

    with patch.object(client, "_get_market_cap_rows", side_effect=fake_get_rows):
        result = client.get_market_cap_list()

    assert call_count == 2
    symbols = [s for s, _ in result]
    caps = [c for _, c in result]
    assert symbols == ["005930", "000660", "035720", "263750"]
    assert caps == sorted(caps, reverse=True)


def test_get_market_cap_list_dedup_keep_max():
    """KOSPI/KOSDAQ 중복 종목은 큰 시총 값 유지."""
    client = KISApiClient(settings=_make_settings())

    # 같은 종목이 양쪽 시장에 다른 cap으로 존재하는 경우
    kospi_rows = [_make_cap_row("005930", 4000000)]
    kosdaq_rows = [_make_cap_row("005930", 3000000)]  # 낮은 값

    with patch.object(client, "_get_market_cap_rows", side_effect=[kospi_rows, kosdaq_rows]):
        result = client.get_market_cap_list()

    assert len(result) == 1
    assert result[0] == ("005930", 4000000)


def test_get_market_cap_list_skip_zero_cap():
    """시총 0인 항목 제외."""
    client = KISApiClient(settings=_make_settings())

    rows = [
        _make_cap_row("005930", 4000000),
        _make_cap_row("000000", 0),
        {"mksc_shrn_iscd": "", "stck_avls": "9999"},  # 빈 종목코드
    ]

    with patch.object(client, "_get_market_cap_rows", return_value=rows):
        result = client.get_market_cap_list()

    symbols = [s for s, _ in result]
    assert "000000" not in symbols
    assert "" not in symbols
    assert "005930" in symbols
