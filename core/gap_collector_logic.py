"""Pure logic for gap-up recovery intraday strategy (EOD simulation)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Dict, FrozenSet, List, Optional, Sequence

from core.gap_naver_ticks import MARKET_CLOSE_HHMMSS, MARKET_OPEN_HHMMSS, in_regular_session
from core.vi_collector_logic import trading_value_to_billion_won


@dataclass(frozen=True)
class GapCandidate:
    symbol: str
    open_price: int
    prev_close: int
    gap_pct: float
    close_price: int


@dataclass(frozen=True)
class GapTradeResult:
    symbol: str
    gap_pct: float
    max_dip_pct: float
    buy_hhmmss: str
    buy_price: int
    sell_hhmmss: str
    sell_price: int
    sell_reason: str  # trailing | close
    qty: int
    close_price: int
    trading_value_won: int


def ymd_to_dot(ymd: str) -> str:
    text = str(ymd or "").strip().replace("-", "").replace(".", "")
    if len(text) != 8 or not text.isdigit():
        return ""
    return f"{text[:4]}.{text[4:6]}.{text[6:8]}"


def dot_to_ymd(dot: str) -> str:
    return str(dot or "").replace(".", "").strip()


def gap_pct_from_ohlc(open_px: int, prev_close: int) -> Optional[float]:
    if open_px <= 0 or prev_close <= 0:
        return None
    return round((open_px / prev_close - 1.0) * 100.0, 1)


def find_gap_candidate(
    bars: Sequence[Dict[str, object]],
    target_ymd: str,
    *,
    gap_min_pct: float = 3.0,
    gap_max_pct: float = 9.0,
) -> Optional[GapCandidate]:
    """
    bars: newest-first OHLCV (Naver history_cache format).
    Returns candidate when target day's open gaps up within [gap_min, gap_max].
    """
    dot = ymd_to_dot(target_ymd)
    if not dot or not bars:
        return None

    today_idx: Optional[int] = None
    for i, bar in enumerate(bars):
        if str(bar.get("date", "") or "").strip() == dot:
            today_idx = i
            break
    if today_idx is None or today_idx + 1 >= len(bars):
        return None

    today = bars[today_idx]
    prev = bars[today_idx + 1]
    open_px = _safe_int(today.get("open"))
    prev_close = _safe_int(prev.get("close"))
    close_px = _safe_int(today.get("close"))
    gap = gap_pct_from_ohlc(open_px, prev_close)
    if gap is None or gap < gap_min_pct or gap > gap_max_pct:
        return None

    symbol = str(today.get("symbol") or bars[0].get("symbol") or "").strip()
    return GapCandidate(
        symbol=symbol,
        open_price=open_px,
        prev_close=prev_close,
        gap_pct=gap,
        close_price=close_px,
    )


def scan_gap_candidates_from_cache(
    cache_entries: Dict[str, List[Dict[str, object]]],
    target_ymd: str,
    *,
    gap_min_pct: float = 3.0,
    gap_max_pct: float = 9.0,
) -> List[GapCandidate]:
    out: List[GapCandidate] = []
    for symbol, bars in sorted(cache_entries.items()):
        if not bars:
            continue
        tagged = [{**b, "symbol": symbol} for b in bars]
        cand = find_gap_candidate(
            tagged,
            target_ymd,
            gap_min_pct=gap_min_pct,
            gap_max_pct=gap_max_pct,
        )
        if cand is not None:
            out.append(GapCandidate(
                symbol=symbol,
                open_price=cand.open_price,
                prev_close=cand.prev_close,
                gap_pct=cand.gap_pct,
                close_price=cand.close_price,
            ))
    return out


def _bar_low(bar: Dict[str, object]) -> int:
    low = _safe_int(bar.get("low"))
    if low > 0:
        return low
    return _safe_int(bar.get("price"))


def _bar_high(bar: Dict[str, object]) -> int:
    high = _safe_int(bar.get("high"))
    if high > 0:
        return high
    return _safe_int(bar.get("price"))


def simulate_gap_trade(
    open_px: int,
    minute_bars: Sequence[Dict[str, object]],
    *,
    dip_min_pct: float = 3.0,
    trailing_stop_pct: float = 0.05,
    qty: int = 1,
    close_price: int = 0,
) -> Optional[GapTradeResult]:
    """
    Intraday simulation:
    1) open_px established (from daily bar)
    2) dip >= dip_min_pct below open before buy
    3) buy at open_px when high recovers to open (limit fill assumed)
    4) trailing sell at peak * (1 - trailing_stop_pct), else close
    """
    if open_px <= 0 or not minute_bars or qty <= 0:
        return None
    if trailing_stop_pct <= 0 or trailing_stop_pct >= 1.0:
        return None

    sorted_bars = sorted(minute_bars, key=lambda b: str(b.get("hhmmss", "") or ""))

    def _session_bar(bar: Dict[str, object]) -> bool:
        return in_regular_session(
            str(bar.get("hhmmss", "") or ""),
            open_hhmmss=MARKET_OPEN_HHMMSS,
            close_hhmmss=MARKET_CLOSE_HHMMSS,
        )

    dipped = False
    max_dip_before_buy = 0.0
    buy_hhmmss = ""
    buy_idx = -1

    for idx, bar in enumerate(sorted_bars):
        if not _session_bar(bar):
            continue
        low = _bar_low(bar)
        high = _bar_high(bar)
        if low <= 0 or high <= 0:
            continue

        dip_pct = (open_px - low) / open_px * 100.0
        if dip_pct >= dip_min_pct:
            dipped = True
        max_dip_before_buy = max(max_dip_before_buy, dip_pct)

        if dipped and high >= open_px:
            buy_hhmmss = str(bar.get("hhmmss", "") or "").strip()
            buy_idx = idx
            break

    if buy_idx < 0 or not buy_hhmmss:
        return None

    peak = open_px
    sell_hhmmss = ""
    sell_price = 0
    sell_reason = "close"

    for bar in sorted_bars[buy_idx:]:
        if not _session_bar(bar):
            continue
        hhmmss = str(bar.get("hhmmss", "") or "").strip()
        if not hhmmss:
            continue
        low = _bar_low(bar)
        high = _bar_high(bar)
        price = _safe_int(bar.get("price"))
        if low <= 0:
            continue

        peak = max(peak, high, price)
        stop_px = int(peak * (1.0 - trailing_stop_pct))
        if stop_px > 0 and low <= stop_px:
            sell_hhmmss = hhmmss
            sell_price = stop_px
            sell_reason = "trailing"
            break

    if not sell_hhmmss:
        if close_price > 0:
            sell_price = close_price
        else:
            last = sorted_bars[-1]
            sell_price = _safe_int(last.get("price"))
        sell_hhmmss = MARKET_CLOSE_HHMMSS
        sell_reason = "close"

    if sell_price <= 0:
        return None

    session_bars = [b for b in sorted_bars if _session_bar(b)]
    tv_won = max((_safe_int(b.get("acml_tr_pbmn")) for b in session_bars), default=0)

    return GapTradeResult(
        symbol="",
        gap_pct=0.0,
        max_dip_pct=round(max_dip_before_buy, 1),
        buy_hhmmss=buy_hhmmss,
        buy_price=open_px,
        sell_hhmmss=sell_hhmmss,
        sell_price=sell_price,
        sell_reason=sell_reason,
        qty=qty,
        close_price=close_price,
        trading_value_won=tv_won,
    )


def hhmmss_to_time(hhmmss: str) -> time:
    text = "".join(ch for ch in str(hhmmss or "") if ch.isdigit())
    if len(text) < 4:
        return time(0, 0, 0)
    if len(text) == 4:
        text += "00"
    elif len(text) == 5:
        text += "0"
    text = text[:6].ljust(6, "0")
    return time(int(text[:2]), int(text[2:4]), int(text[4:6]))


def ymd_to_date(ymd: str) -> date:
    text = str(ymd or "").strip().replace("-", "").replace(".", "")
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def bar_for_ymd(bars: Sequence[Dict[str, object]], ymd: str) -> Optional[Dict[str, object]]:
    dot = ymd_to_dot(ymd)
    if not dot:
        return None
    for bar in bars:
        if str(bar.get("date", "") or "").strip() == dot:
            return bar
    return None


def bar_volume_for_ymd(bars: Sequence[Dict[str, object]], ymd: str) -> int:
    bar = bar_for_ymd(bars, ymd)
    if not bar:
        return 0
    return _safe_int(bar.get("volume"))


def bar_open_for_ymd(bars: Sequence[Dict[str, object]], ymd: str) -> int:
    bar = bar_for_ymd(bars, ymd)
    if not bar:
        return 0
    return _safe_int(bar.get("open"))


def latest_close_from_bars(bars: Sequence[Dict[str, object]]) -> int:
    """history_cache bars are newest-first."""
    if not bars:
        return 0
    return _safe_int(bars[0].get("close"))


def calc_volume_approx_fields(
    open_px: int,
    daily_volume: int,
    *,
    market_cap_billion: int | None = None,
    current_price: int | None = None,
) -> Dict[str, object]:
    """
    Approximate volume/cap fields for gap result xlsx.
    shares_outstanding uses current market cap and current price.
    """
    approx_tv_won = open_px * daily_volume if open_px > 0 and daily_volume > 0 else 0
    shares: int | None = None
    price_for_shares = current_price if current_price and current_price > 0 else 0
    if market_cap_billion and market_cap_billion > 0 and price_for_shares > 0:
        cap_won = int(market_cap_billion) * 100_000_000
        shares = int(cap_won / price_for_shares)
    approx_cap_billion: int | None = None
    if shares and shares > 0 and open_px > 0:
        approx_cap_billion = int(shares * open_px / 100_000_000)
    return {
        "daily_volume": daily_volume if daily_volume > 0 else None,
        "approx_trading_value_won": approx_tv_won if approx_tv_won > 0 else None,
        "approx_trading_value_billion": (
            trading_value_to_billion_won(approx_tv_won) if approx_tv_won > 0 else None
        ),
        "shares_outstanding": shares,
        "approx_market_cap_billion": approx_cap_billion,
    }


def calc_next_open_return_pct(
    buy_ymd: str,
    buy_price: int,
    qty: int,
    symbol_bars: Sequence[Dict[str, object]],
    holidays: FrozenSet[str],
    *,
    fee_rate_buy: float,
    fee_rate_sell: float,
    tax_rate_sell: float,
) -> float | None:
    """Hypothetical return if sold at next trading day's open (None if bar missing)."""
    if not symbol_bars:
        return None
    from core.xlsx_price_track import next_trading_day

    next_ymd = next_trading_day(buy_ymd, holidays)
    next_open = bar_open_for_ymd(symbol_bars, next_ymd)
    if next_open <= 0:
        return None
    next_amounts = calc_trade_amounts(
        buy_price,
        next_open,
        qty,
        fee_rate_buy=fee_rate_buy,
        fee_rate_sell=fee_rate_sell,
        tax_rate_sell=tax_rate_sell,
    )
    return float(next_amounts["return_pct"])


def build_gap_result_row(
    cand: GapCandidate,
    trade: GapTradeResult,
    *,
    buy_ymd: str,
    name: str = "",
    fee_rate_buy: float,
    fee_rate_sell: float,
    tax_rate_sell: float,
    market_cap_billion: int | None = None,
    trading_value_won: int = 0,
    trading_value_billion: int | None = None,
    daily_volume: int | None = None,
    current_price: int | None = None,
    symbol_bars: Sequence[Dict[str, object]] | None = None,
    holidays: FrozenSet[str] | None = None,
) -> Dict[str, object]:
    amounts = calc_trade_amounts(
        trade.buy_price,
        trade.sell_price,
        trade.qty,
        fee_rate_buy=fee_rate_buy,
        fee_rate_sell=fee_rate_sell,
        tax_rate_sell=tax_rate_sell,
    )
    close_amounts = calc_trade_amounts(
        trade.buy_price,
        cand.close_price,
        trade.qty,
        fee_rate_buy=fee_rate_buy,
        fee_rate_sell=fee_rate_sell,
        tax_rate_sell=tax_rate_sell,
    )
    next_open_return_pct = calc_next_open_return_pct(
        buy_ymd,
        trade.buy_price,
        trade.qty,
        symbol_bars or [],
        holidays or frozenset(),
        fee_rate_buy=fee_rate_buy,
        fee_rate_sell=fee_rate_sell,
        tax_rate_sell=tax_rate_sell,
    )

    row: Dict[str, object] = {
        "buy_ymd": buy_ymd,
        "sell_ymd": buy_ymd,
        "buy_time": hhmmss_to_time(trade.buy_hhmmss),
        "sell_time": hhmmss_to_time(trade.sell_hhmmss),
        "symbol": cand.symbol,
        "name": name,
        "gap_pct": cand.gap_pct,
        "max_dip_pct": trade.max_dip_pct,
        "buy_price": trade.buy_price,
        "sell_price": trade.sell_price,
        "qty": trade.qty,
        "buy_amount": amounts["buy_amount"],
        "sell_amount": amounts["sell_amount"],
        "pnl": amounts["pnl"],
        "return_pct": amounts["return_pct"],
        "tax": amounts["tax"],
        "fee_total": amounts["fee_total"],
        "sell_reason": trade.sell_reason,
        "close_sell": 1 if trade.sell_reason == "close" else 0,
        "close_only_return_pct": close_amounts["return_pct"],
        "next_open_return_pct": next_open_return_pct,
    }
    if market_cap_billion is not None:
        row["market_cap_billion"] = market_cap_billion
    if trading_value_won:
        row["trading_value_won"] = trading_value_won
    if trading_value_billion is not None:
        row["trading_value_billion"] = trading_value_billion
    vol_fields = calc_volume_approx_fields(
        cand.open_price,
        daily_volume or 0,
        market_cap_billion=market_cap_billion,
        current_price=current_price,
    )
    for key, val in vol_fields.items():
        if val is not None:
            row[key] = val
    return row


def calc_trade_amounts(
    buy_price: int,
    sell_price: int,
    qty: int,
    *,
    fee_rate_buy: float,
    fee_rate_sell: float,
    tax_rate_sell: float,
) -> Dict[str, float]:
    buy_amount = buy_price * qty
    sell_amount = sell_price * qty
    fee_buy = buy_amount * fee_rate_buy
    fee_sell = sell_amount * fee_rate_sell
    tax = sell_amount * tax_rate_sell
    pnl = sell_amount - buy_amount - fee_buy - fee_sell - tax
    ret_pct = (pnl / buy_amount * 100.0) if buy_amount > 0 else 0.0
    return {
        "buy_amount": float(buy_amount),
        "sell_amount": float(sell_amount),
        "fee_buy": round(fee_buy, 1),
        "fee_sell": round(fee_sell, 1),
        "fee_total": round(fee_buy + fee_sell, 1),
        "tax": round(tax, 1),
        "pnl": round(pnl, 1),
        "return_pct": round(ret_pct, 2),
    }


def _safe_int(val: object, default: int = 0) -> int:
    try:
        if val is None:
            return default
        text = str(val).replace(",", "").strip()
        if not text:
            return default
        return abs(int(float(text)))
    except Exception:
        return default
