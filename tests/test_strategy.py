from core.strategy import W52HighStrategy


TRAILING_STOP = 0.075


def test_entry_signal_on_w52_high_touch():
    """52주 신고가 터치 + 거래량 충족 시 매수 신호 발생."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)

    # 신고가 미달: 신호 없음
    sig = s.on_quote("005930", current_price=69999, current_volume=2000)
    assert sig is None

    # 신고가 터치 + 거래량 충족: 신호 발생
    sig = s.on_quote("005930", current_price=70000, current_volume=1000)
    assert sig is not None
    assert sig.symbol == "005930"
    assert sig.entry_price == 70000


def test_no_entry_when_volume_insufficient():
    """거래량 미충족 시 신호 없음."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)

    sig = s.on_quote("005930", current_price=70000, current_volume=999)
    assert sig is None


def test_no_double_entry():
    """이미 매수된 종목은 다시 신호 안 나옴."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)

    s.on_quote("005930", current_price=70000, current_volume=1000)
    sig = s.on_quote("005930", current_price=71000, current_volume=2000)
    assert sig is None


def test_trailing_stop_triggers():
    """최고가 대비 trailing_stop_pct 이상 하락 시 매도 신호 발생."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)
    s.on_quote("005930", current_price=70000, current_volume=1000)
    s.register_position("005930", buy_price=70000, qty=10)

    # 최고가 80000 도달
    s.on_position_quote("005930", current_price=80000)

    # 80000 * (1 - 0.075) = 74000: 74000 이하 하락 시 매도
    sell = s.on_position_quote("005930", current_price=73999)
    assert sell is not None
    assert sell.symbol == "005930"
    assert sell.qty == 10
    assert sell.reason == "trailing_stop"


def test_trailing_stop_not_triggered_before_threshold():
    """최고가 대비 하락이 기준 미만이면 매도 신호 없음."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)
    s.on_quote("005930", current_price=70000, current_volume=1000)
    s.register_position("005930", buy_price=70000, qty=10)

    s.on_position_quote("005930", current_price=80000)

    # 80000 * (1 - 0.074) = 74080: 아직 기준 미달
    sell = s.on_position_quote("005930", current_price=74080)
    assert sell is None


def test_peak_price_updates():
    """최고가가 계속 갱신되어야 트레일링이 올바르게 작동."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)
    s.on_quote("005930", current_price=70000, current_volume=1000)
    s.register_position("005930", buy_price=70000, qty=5)

    s.on_position_quote("005930", current_price=75000)
    s.on_position_quote("005930", current_price=90000)  # 최고가 갱신

    # 90000 * (1 - 0.075) = 83250: 83250 이하 시 매도
    sell = s.on_position_quote("005930", current_price=83249)
    assert sell is not None


def test_volume_insufficient_can_retry_next_tick():
    """거래량 미충족 tick 이후에도 다음 tick에서 신호 발생 가능 (영구 skip 없음)."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)

    # 첫 tick: 신고가 터치하지만 거래량 부족 → 신호 없음
    sig = s.on_quote("005930", current_price=70000, current_volume=500)
    assert sig is None

    # 종목이 watchlist에서 제거되지 않고 남아 있어야 함
    assert "005930" in s.watchlist_symbols()

    # 두 번째 tick: 거래량 충족 → 신호 발생
    sig = s.on_quote("005930", current_price=70000, current_volume=1000)
    assert sig is not None
    assert sig.entry_price == 70000


def test_watchlist_symbols_excludes_only_bought():
    """watchlist_symbols()는 bought된 종목만 제외."""
    s = W52HighStrategy(trailing_stop_pct=TRAILING_STOP)
    s.register("005930", w52_high=70000, vol_ma20=1000)
    s.register("000660", w52_high=80000, vol_ma20=2000)

    # 첫 tick: 005930 매수 완료
    s.on_quote("005930", current_price=70000, current_volume=1500)

    wl = s.watchlist_symbols()
    assert "005930" not in wl   # bought → 제외
    assert "000660" in wl       # 미매수 → 유지
