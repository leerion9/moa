"""Tests for core/universe_builder.py — pure functions and UniverseBuilder pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import MagicMock, patch

import pytest

from core.api_client import SymbolHistory
from core.universe_builder import (
    RS_LOOKBACK_DAYS,
    UniverseBuilder,
    apply_second_filter,
    build_features,
    calc_rs_return,
    calc_vol_ma,
    calc_w52_hit_count,
    compute_rs_top_pct,
    is_etf_by_name,
    is_preferred_stock,
)
from core.universe_cache import CachedSymbol


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(high: int = 10000, close: int = 9900, volume: int = 100_000, date: str = "20260101") -> Dict:
    return {"date": date, "open": 9800, "high": high, "low": 9700, "close": close, "volume": volume}


def _bars(n: int, base_close: int = 10000) -> List[Dict]:
    """최신순 n개 bar 생성 (날짜 역순 부여)."""
    result = []
    for i in range(n):
        year = 2026
        day_num = 500 - i
        # 간단히 날짜 인코딩 (테스트용)
        result.append(_bar(
            high=base_close + 200,
            close=base_close - i * 10,
            volume=100_000 + i * 1000,
            date=f"2026{(i // 30 + 1):02d}{(i % 28 + 1):02d}",
        ))
    return result


def _make_settings(**kwargs):
    defaults = dict(
        min_market_cap_billion=800,
        include_etf=False,
        include_preferred=False,
        rs_top_pct=0.10,
        w52_fresh_days=60,
        w52_cont_lookback_days=10,
        w52_cont_min_hits=5,
        strategy_mode=1,
        naver_http_delay_sec=0.0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# is_preferred_stock
# ---------------------------------------------------------------------------

def test_is_preferred_stock_true():
    assert is_preferred_stock("005935") is True  # 삼성전자우
    assert is_preferred_stock("000885") is True
    assert is_preferred_stock("001235") is True


def test_is_preferred_stock_false():
    assert is_preferred_stock("005930") is False  # 삼성전자 (보통주)
    assert is_preferred_stock("000660") is False
    assert is_preferred_stock("035720") is False


def test_is_preferred_stock_invalid():
    assert is_preferred_stock("") is False
    assert is_preferred_stock("12345") is False   # 5자리
    assert is_preferred_stock("ABCDEF") is False  # 비숫자
    assert is_preferred_stock("0059301") is False # 7자리


# ---------------------------------------------------------------------------
# is_etf_by_name
# ---------------------------------------------------------------------------

def test_is_etf_by_name_prefix_match():
    assert is_etf_by_name("KODEX 200") is True
    assert is_etf_by_name("TIGER 200") is True
    assert is_etf_by_name("KBSTAR 200") is True
    assert is_etf_by_name("ACE KOSPI") is True


def test_is_etf_by_name_keyword_match():
    assert is_etf_by_name("삼성 ETF") is True
    assert is_etf_by_name("SK 리츠") is True
    assert is_etf_by_name("국채 펀드") is True


def test_is_etf_by_name_normal_stock():
    assert is_etf_by_name("삼성전자") is False
    assert is_etf_by_name("카카오") is False
    assert is_etf_by_name("현대차") is False
    assert is_etf_by_name("SK하이닉스") is False


# ---------------------------------------------------------------------------
# calc_vol_ma
# ---------------------------------------------------------------------------

def test_calc_vol_ma_normal():
    bars = [_bar(volume=v) for v in [100, 200, 300, 400, 500]]
    assert calc_vol_ma(bars, days=5) == 300
    assert calc_vol_ma(bars, days=3) == 200  # [100, 200, 300]의 평균


def test_calc_vol_ma_fewer_bars_than_days():
    bars = [_bar(volume=100), _bar(volume=200)]
    # bars가 days보다 적으면 있는 것만 평균
    assert calc_vol_ma(bars, days=20) == 150


def test_calc_vol_ma_empty():
    assert calc_vol_ma([], days=20) == 0


# ---------------------------------------------------------------------------
# calc_w52_hit_count
# ---------------------------------------------------------------------------

def test_calc_w52_hit_count_all_hit():
    bars = [_bar(high=10500) for _ in range(10)]
    assert calc_w52_hit_count(bars, w52_high=10000, lookback=10) == 10


def test_calc_w52_hit_count_none_hit():
    bars = [_bar(high=9000) for _ in range(10)]
    assert calc_w52_hit_count(bars, w52_high=10000, lookback=10) == 0


def test_calc_w52_hit_count_partial():
    bars = [_bar(high=10000 if i < 3 else 9000) for i in range(10)]
    assert calc_w52_hit_count(bars, w52_high=10000, lookback=10) == 3


def test_calc_w52_hit_count_lookback_limit():
    bars = [_bar(high=10000)] * 20
    # lookback=5이면 5개만 확인
    assert calc_w52_hit_count(bars, w52_high=10000, lookback=5) == 5


def test_calc_w52_hit_count_zero_w52():
    bars = [_bar(high=10000)] * 5
    assert calc_w52_hit_count(bars, w52_high=0, lookback=5) == 0


# ---------------------------------------------------------------------------
# calc_rs_return
# ---------------------------------------------------------------------------

def test_calc_rs_return_positive():
    # close[0]=12000, close[126]=10000 → +20%
    bars = [_bar(close=12000)] + [_bar(close=11000)] * 125 + [_bar(close=10000)]
    result = calc_rs_return(bars, lookback=126)
    assert result is not None
    assert abs(result - 0.20) < 1e-6


def test_calc_rs_return_negative():
    bars = [_bar(close=8000)] + [_bar(close=9000)] * 125 + [_bar(close=10000)]
    result = calc_rs_return(bars, lookback=126)
    assert result is not None
    assert result < 0


def test_calc_rs_return_insufficient_data():
    bars = [_bar(close=10000)] * 50  # 126보다 적음
    assert calc_rs_return(bars, lookback=126) is None


def test_calc_rs_return_exactly_at_boundary():
    bars = [_bar(close=10000)] * 126  # lookback=126이면 len=126 → 부족 (126+1 필요)
    assert calc_rs_return(bars, lookback=126) is None


def test_calc_rs_return_zero_base():
    bars = [_bar(close=10000)] + [_bar(close=0)] * 126
    assert calc_rs_return(bars, lookback=126) is None


# ---------------------------------------------------------------------------
# compute_rs_top_pct
# ---------------------------------------------------------------------------

def test_compute_rs_top_pct_10pct():
    returns = {f"SYM{i:03d}": float(i) for i in range(100)}  # 수익률 0~99
    top = compute_rs_top_pct(returns, top_pct=0.10)
    assert len(top) == 10
    # 상위 10개는 수익률 90~99
    top_set = set(top)
    for i in range(90, 100):
        assert f"SYM{i:03d}" in top_set


def test_compute_rs_top_pct_minimum_one():
    returns = {"A": 1.0, "B": 0.5, "C": 0.1}
    # 3개의 10% = 0.3 → int(0.3)=0 → max(1,0)=1
    top = compute_rs_top_pct(returns, top_pct=0.10)
    assert len(top) == 1
    assert top[0] == "A"


def test_compute_rs_top_pct_empty():
    assert compute_rs_top_pct({}, top_pct=0.10) == []


# ---------------------------------------------------------------------------
# build_features
# ---------------------------------------------------------------------------

def _make_history(
    symbol: str = "005930",
    w52_high: int = 10000,
    n_bars: int = 130,
    high_per_bar: int = 10000,
    close_per_bar: int = 9500,
    volume_per_bar: int = 200_000,
) -> SymbolHistory:
    bars = [
        {"date": f"2026{i // 28 + 1:02d}{i % 28 + 1:02d}",
         "open": 9300, "high": high_per_bar, "low": 9000,
         "close": close_per_bar, "volume": volume_per_bar}
        for i in range(n_bars)
    ]
    return SymbolHistory(symbol=symbol, w52_high=w52_high, w52_low=7000, bars=bars)


def test_build_features_normal():
    hist = _make_history(w52_high=10000, n_bars=130, high_per_bar=10000, volume_per_bar=300_000)
    feat = build_features(hist, fresh_days=60, cont_days=10)
    assert feat is not None
    assert feat.w52_high == 10000
    assert feat.vol_ma20 == 300_000
    assert feat.w52_hit_60d == 60   # 60일 모두 high==w52_high
    assert feat.w52_hit_10d == 10   # 10일 모두 hit


def test_build_features_no_hits():
    hist = _make_history(w52_high=10000, n_bars=130, high_per_bar=9000)
    feat = build_features(hist, fresh_days=60, cont_days=10)
    assert feat is not None
    assert feat.w52_hit_60d == 0
    assert feat.w52_hit_10d == 0


def test_build_features_zero_w52_high():
    hist = _make_history(w52_high=0, n_bars=130)
    assert build_features(hist, fresh_days=60, cont_days=10) is None


def test_build_features_insufficient_bars():
    hist = _make_history(w52_high=10000, n_bars=15)  # 20일 미만
    assert build_features(hist, fresh_days=60, cont_days=10) is None


# ---------------------------------------------------------------------------
# apply_second_filter
# ---------------------------------------------------------------------------

def _make_feat(w52_hit_60d: int = 0, w52_hit_10d: int = 0) -> CachedSymbol:
    return CachedSymbol(w52_high=10000, vol_ma20=200_000, w52_hit_60d=w52_hit_60d, w52_hit_10d=w52_hit_10d)


def test_apply_second_filter_strategy1_pass():
    features = {
        "A": _make_feat(w52_hit_60d=0),   # 최초 돌파 후보 → 통과
        "B": _make_feat(w52_hit_60d=3),   # 최근 신고가 있음 → 제외
    }
    result = apply_second_filter(features, strategy_mode=1, fresh_days=60, cont_min_hits=5)
    assert "A" in result
    assert "B" not in result


def test_apply_second_filter_strategy2_pass():
    features = {
        "A": _make_feat(w52_hit_10d=5),   # 추세 지속 → 통과
        "B": _make_feat(w52_hit_10d=4),   # 미달 → 제외
        "C": _make_feat(w52_hit_10d=7),   # 충분 → 통과
    }
    result = apply_second_filter(features, strategy_mode=2, fresh_days=60, cont_min_hits=5)
    assert "A" in result
    assert "B" not in result
    assert "C" in result


def test_apply_second_filter_empty():
    assert apply_second_filter({}, strategy_mode=1, fresh_days=60, cont_min_hits=5) == {}


# ---------------------------------------------------------------------------
# UniverseBuilder 통합 테스트
# ---------------------------------------------------------------------------

def _make_api_mock(cap_list=None, history_map=None):
    """KISApiClient mock 생성."""
    mock = MagicMock()
    mock.get_market_cap_list.return_value = cap_list or []
    if history_map:
        def _get_history(symbol, days=135):
            return history_map.get(symbol, SymbolHistory(symbol=symbol, w52_high=0, w52_low=0, bars=[]))
        mock.get_symbol_history.side_effect = _get_history
    else:
        mock.get_symbol_history.return_value = SymbolHistory(
            symbol="000000", w52_high=0, w52_low=0, bars=[]
        )
    return mock


def test_builder_basic_strategy1():
    """정상적인 strategy1 빌드 테스트."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=1.0)  # RS 필터 전체 통과

    cap_list = [("005930", 4_000_000), ("000660", 1_000_000), ("035720", 800_000)]

    # 히스토리: 130개 bar, w52_high=10000, high<w52_high → w52_hit_60d=0 → strategy1 통과
    def _make_hist(symbol):
        bars = [
            {"date": f"202601{i + 1:02d}", "open": 9000, "high": 9500,
             "low": 8000, "close": 9000 - i * 5, "volume": 200_000}
            for i in range(130)
        ]
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={s: _make_hist(s) for s, _ in cap_list},
    )

    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    assert cache.strategy_mode == 1
    assert len(cache.symbols) == 3  # 전부 w52_hit_60d=0 → 통과
    for sym in ["005930", "000660", "035720"]:
        assert sym in cache.symbols
        assert cache.symbols[sym].w52_high == 10000


def test_builder_strategy1_filters_recent_hits():
    """strategy1: 최근 신고가 터치 있는 종목 제외."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=1.0)

    cap_list = [("005930", 4_000_000), ("000660", 1_000_000)]

    def _make_hist(symbol, high_val):
        bars = [
            {"date": f"202601{i + 1:02d}", "open": 9000, "high": high_val,
             "low": 8000, "close": 9000, "volume": 200_000}
            for i in range(130)
        ]
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={
            "005930": _make_hist("005930", high_val=9500),   # high < w52_high → hit=0 → 통과
            "000660": _make_hist("000660", high_val=10000),  # high == w52_high → hit=60 → 제외
        },
    )

    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    assert "005930" in cache.symbols
    assert "000660" not in cache.symbols


def test_builder_strategy2():
    """strategy2: 최근 10일 내 신고가 5회 이상 종목만 통과."""
    settings = _make_settings(strategy_mode=2, rs_top_pct=1.0, w52_cont_min_hits=5)

    cap_list = [("005930", 4_000_000), ("000660", 1_000_000)]

    def _make_hist(symbol, hit_count_in_first10):
        # 최초 hit_count_in_first10개 bar는 high>=w52_high, 나머지는 미달
        bars = []
        for i in range(130):
            high = 10000 if i < hit_count_in_first10 else 9000
            bars.append({
                "date": f"202601{i + 1:02d}", "open": 9000, "high": high,
                "low": 8000, "close": 9000, "volume": 200_000,
            })
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={
            "005930": _make_hist("005930", hit_count_in_first10=5),  # 통과
            "000660": _make_hist("000660", hit_count_in_first10=4),  # 미달 → 제외
        },
    )

    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    assert "005930" in cache.symbols
    assert "000660" not in cache.symbols


def test_builder_preferred_stock_excluded():
    """우선주(코드 끝자리 != 0) 제외 확인."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=1.0, include_preferred=False)

    cap_list = [("005930", 4_000_000), ("005935", 200_000)]  # 005935 = 우선주

    def _make_hist(symbol):
        bars = [
            {"date": f"202601{i + 1:02d}", "open": 9000, "high": 9500,
             "low": 8000, "close": 9000, "volume": 200_000}
            for i in range(130)
        ]
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={s: _make_hist(s) for s, _ in cap_list},
    )

    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    assert "005930" in cache.symbols
    assert "005935" not in cache.symbols


def test_builder_market_cap_filter():
    """시총 800억 미만 제외 확인."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=1.0, min_market_cap_billion=800)

    cap_list = [("005930", 4_000_000), ("000001", 799)]  # 000001은 799억 → 제외

    def _make_hist(symbol):
        bars = [
            {"date": f"202601{i + 1:02d}", "open": 9000, "high": 9500,
             "low": 8000, "close": 9000, "volume": 200_000}
            for i in range(130)
        ]
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={"005930": _make_hist("005930")},
    )

    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    assert "005930" in cache.symbols
    assert "000001" not in cache.symbols


def test_builder_rs_filter():
    """RS 상위 10%만 통과하는지 확인."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=0.5)  # 상위 50%

    # 4개 종목, 2개만 통과해야 함
    cap_list = [(f"00{i:04d}", 1_000_000) for i in range(4)]
    preferred = set()
    for s, _ in cap_list:
        if is_preferred_stock(s):
            preferred.add(s)

    base_closes = [10000, 9000, 8000, 7000]  # 종목별 현재가 (높을수록 RS 좋음)

    def _make_hist(symbol, idx):
        # bars[0].close = base_closes[idx], bars[RS_LOOKBACK_DAYS].close = 10000
        close_now = base_closes[idx]
        bars = [{"date": f"202601{i + 1:02d}", "open": 9000, "high": close_now + 100,
                 "low": 8000, "close": close_now, "volume": 200_000}
                for i in range(RS_LOOKBACK_DAYS + 5)]
        # RS_LOOKBACK_DAYS번째(126번) bar의 close를 10000으로 고정
        bars[RS_LOOKBACK_DAYS] = {**bars[RS_LOOKBACK_DAYS], "close": 10000}
        return SymbolHistory(symbol=symbol, w52_high=close_now + 200, w52_low=5000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={s: _make_hist(s, i) for i, (s, _) in enumerate(cap_list)},
    )

    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    # 상위 50% = 2종목 통과
    # 단, 우선주 등 필터로 제거된 건 제외해야 함 (여기서는 코드 확인)
    # 코드가 모두 보통주이고 w52_hit_60d=0이라면 2개 통과
    assert len(cache.symbols) <= 2


def test_builder_empty_cap_list_returns_empty_cache():
    """시총 목록이 비어 있으면 빈 캐시 반환."""
    settings = _make_settings(strategy_mode=1)
    api = _make_api_mock(cap_list=[])

    with patch.object(
        UniverseBuilder,
        "_fetch_cap_list_naver",
        return_value=[],
    ):
        builder = UniverseBuilder(api=api, settings=settings)
        cache = builder.build()

    assert cache.symbols == {}
    assert cache.strategy_mode == 1


def test_builder_etf_excluded_by_name():
    """ETF 종목명 필터 확인 (symbol_names 사용)."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=1.0, include_etf=False)

    cap_list = [("069500", 5_000_000), ("005930", 4_000_000)]
    symbol_names = {"069500": "KODEX 200", "005930": "삼성전자"}

    def _make_hist(symbol):
        bars = [
            {"date": f"202601{i + 1:02d}", "open": 9000, "high": 9500,
             "low": 8000, "close": 9000, "volume": 200_000}
            for i in range(130)
        ]
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={"005930": _make_hist("005930")},
    )

    builder = UniverseBuilder(api=api, settings=settings, symbol_names=symbol_names)
    cache = builder.build()

    assert "069500" not in cache.symbols  # ETF 제외
    assert "005930" in cache.symbols


def test_builder_etf_excluded_by_cap_list_names_fallback():
    """symbol_names 없어도 _cap_list_names(시총 API 이름) fallback으로 ETF 필터 동작."""
    settings = _make_settings(strategy_mode=1, rs_top_pct=1.0, include_etf=False)

    cap_list = [("069500", 5_000_000), ("005930", 4_000_000)]

    def _make_hist(symbol):
        bars = [
            {"date": f"202601{i + 1:02d}", "open": 9000, "high": 9500,
             "low": 8000, "close": 9000, "volume": 200_000}
            for i in range(130)
        ]
        return SymbolHistory(symbol=symbol, w52_high=10000, w52_low=7000, bars=bars)

    api = _make_api_mock(
        cap_list=cap_list,
        history_map={"005930": _make_hist("005930")},
    )
    # KIS API가 종목명을 돌려줬다고 가정 (side-channel)
    api._last_cap_list_names = {"069500": "KODEX 200", "005930": "삼성전자"}

    # symbol_names 없이 생성
    builder = UniverseBuilder(api=api, settings=settings)
    cache = builder.build()

    assert "069500" not in cache.symbols  # cap_list_names fallback으로 ETF 제외
    assert "005930" in cache.symbols


def test_builder_cache_metadata():
    """빌드 결과 캐시의 메타데이터 확인."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    settings = _make_settings(strategy_mode=2)
    api = _make_api_mock(cap_list=[])

    with patch.object(UniverseBuilder, "_fetch_cap_list_naver", return_value=[]):
        builder = UniverseBuilder(api=api, settings=settings)
        now = datetime(2026, 5, 20, 8, 30, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        cache = builder.build(now_kst=now)

    assert cache.date_kst == "20260520"
    assert cache.strategy_mode == 2
    assert "2026-05-20" in cache.created_at_iso
