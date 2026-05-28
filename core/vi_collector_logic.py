"""Pure logic for static upward VI batch collection (EOD)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

MARKET_CLOSE_HHMMSS = "153000"
SESSION_OPEN_HHMMSS = "090000"

# KIS vi_kind_code: 1=정적, 2=동적 (fallback heuristics below)
_STATIC_KIND = frozenset({"1", "01"})
_DYNAMIC_KIND = frozenset({"2", "02"})


@dataclass(frozen=True)
class ViEventRow:
    symbol: str
    name: str
    market: str
    trigger_hhmmss: str
    release_hhmmss: str
    trigger_price: int
    release_price: int
    has_second_vi: bool
    post_release_high_pct: Optional[float]
    post_release_low_pct: Optional[float]
    market_cap_billion: Optional[int]
    pre_vi_trading_value: Optional[int]
    cap_group: str


def normalize_symbol(raw: object) -> str:
    text = str(raw or "").strip()
    if text.isdigit():
        return text.zfill(6)
    return text


def normalize_hhmmss(raw: object) -> str:
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(digits) >= 6:
        return digits[:6]
    if len(digits) == 4:
        return digits + "00"
    if len(digits) == 5:
        return digits + "0"
    return ""


def format_hhmmss_display(hhmmss: str) -> str:
    if len(hhmmss) != 6:
        return hhmmss
    return f"{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:6]}"


def is_dynamic_vi(row: Dict[str, object]) -> bool:
    kind = str(row.get("vi_kind_code", "") or "").strip()
    if kind in _DYNAMIC_KIND:
        return True
    if kind in _STATIC_KIND:
        return False
    dmc = _safe_int(row.get("vi_dmc_stnd_prc"))
    stnd = _safe_int(row.get("vi_stnd_prc"))
    if dmc > 0 and stnd <= 0:
        return True
    return False


def is_static_vi(row: Dict[str, object]) -> bool:
    kind = str(row.get("vi_kind_code", "") or "").strip()
    if kind in _STATIC_KIND:
        return True
    if kind in _DYNAMIC_KIND:
        return False
    stnd = _safe_int(row.get("vi_stnd_prc"))
    dmc = _safe_int(row.get("vi_dmc_stnd_prc"))
    return stnd > 0 and dmc <= 0


def is_upward_vi(row: Dict[str, object]) -> bool:
    trigger = _safe_int(row.get("vi_prc"))
    ref = _safe_int(row.get("vi_stnd_prc"))
    if trigger > 0 and ref > 0:
        return trigger >= ref
    div = str(row.get("fid_div_cls_code", "") or row.get("FID_DIV_CLS_CODE", "") or "")
    if div == "1":
        return True
    if div == "2":
        return False
    dprt = row.get("vi_dprt")
    try:
        if dprt is not None and str(dprt).strip() != "":
            return float(dprt) >= 0
    except Exception:
        pass
    return True


def market_cap_group(cap_billion: Optional[int]) -> str:
    if cap_billion is None or cap_billion <= 0:
        return ""
    cap = int(cap_billion)
    if cap <= 800:
        return "A"
    if cap <= 2000:
        return "B"
    if cap <= 5000:
        return "C"
    if cap <= 10000:
        return "D"
    if cap <= 50000:
        return "E"
    if cap <= 100000:
        return "F"
    return "G"


def select_static_upward_first_vi(
    static_up_rows: Sequence[Dict[str, object]],
    dynamic_rows: Sequence[Dict[str, object]],
) -> List[Dict[str, object]]:
    """
    Return one raw KIS row per symbol: first static upward VI (vi_count==1),
    excluding symbols with any dynamic VI that day.
    """
    dynamic_symbols = {
        normalize_symbol(r.get("mksc_shrn_iscd", r.get("stck_shrn_iscd", "")))
        for r in dynamic_rows
        if is_dynamic_vi(r)
    }
    by_symbol: Dict[str, List[Dict[str, object]]] = {}
    for row in static_up_rows:
        if not is_static_vi(row):
            continue
        if not is_upward_vi(row):
            continue
        sym = normalize_symbol(row.get("mksc_shrn_iscd", row.get("stck_shrn_iscd", "")))
        if not sym or sym in dynamic_symbols:
            continue
        by_symbol.setdefault(sym, []).append(row)

    selected: List[Dict[str, object]] = []
    for sym, rows in by_symbol.items():
        ordered = sorted(rows, key=lambda r: normalize_hhmmss(r.get("cntg_vi_hour", "")))
        first_candidates = [r for r in ordered if _safe_int(r.get("vi_count")) == 1]
        if first_candidates:
            selected.append(first_candidates[0])
            continue
        if ordered:
            selected.append(ordered[0])
    return sorted(selected, key=lambda r: normalize_hhmmss(r.get("cntg_vi_hour", "")))


def has_second_static_upward_vi(
    symbol: str,
    static_up_rows: Sequence[Dict[str, object]],
) -> bool:
    sym = normalize_symbol(symbol)
    hits = [
        r for r in static_up_rows
        if normalize_symbol(r.get("mksc_shrn_iscd", r.get("stck_shrn_iscd", ""))) == sym
        and is_static_vi(r)
        and is_upward_vi(r)
    ]
    if len(hits) >= 2:
        return True
    return any(_safe_int(r.get("vi_count")) >= 2 for r in hits)


def find_bar_at_or_before(
    bars: Sequence[Dict[str, object]],
    hhmmss: str,
) -> Optional[Dict[str, object]]:
    target = normalize_hhmmss(hhmmss)
    if not target:
        return None
    best: Optional[Dict[str, object]] = None
    best_key = ""
    for bar in bars:
        key = normalize_hhmmss(bar.get("hhmmss", ""))
        if not key or key > target:
            continue
        if best is None or key >= best_key:
            best = bar
            best_key = key
    return best


def bars_after(
    bars: Sequence[Dict[str, object]],
    hhmmss: str,
    *,
    inclusive: bool = False,
) -> List[Dict[str, object]]:
    target = normalize_hhmmss(hhmmss)
    out: List[Dict[str, object]] = []
    for bar in bars:
        key = normalize_hhmmss(bar.get("hhmmss", ""))
        if not key:
            continue
        if inclusive:
            if key >= target:
                out.append(bar)
        elif key > target:
            out.append(bar)
    return out


def estimate_release_price(
    bars: Sequence[Dict[str, object]],
    release_hhmmss: str,
    fallback_trigger_price: int,
) -> int:
    bar = find_bar_at_or_before(bars, release_hhmmss)
    if bar:
        px = _safe_int(bar.get("price"))
        if px > 0:
            return px
    return fallback_trigger_price


def pre_vi_trading_value(
    bars: Sequence[Dict[str, object]],
    trigger_hhmmss: str,
) -> Optional[int]:
    bar = find_bar_at_or_before(bars, trigger_hhmmss)
    if bar is None:
        return None
    val = _safe_int(bar.get("acml_tr_pbmn"))
    return val if val > 0 else None


def post_release_pct_range(
    bars: Sequence[Dict[str, object]],
    release_hhmmss: str,
    release_price: int,
) -> Tuple[Optional[float], Optional[float]]:
    if release_price <= 0:
        return None, None
    segment = bars_after(bars, release_hhmmss, inclusive=True)
    segment = [
        b for b in segment
        if SESSION_OPEN_HHMMSS <= normalize_hhmmss(b.get("hhmmss", "")) <= MARKET_CLOSE_HHMMSS
    ]
    if not segment:
        return None, None
    highs = [_safe_int(b.get("high")) for b in segment if _safe_int(b.get("high")) > 0]
    lows = [_safe_int(b.get("low")) for b in segment if _safe_int(b.get("low")) > 0]
    if not highs or not lows:
        return None, None
    max_high = max(highs)
    min_low = min(lows)
    high_pct = round((max_high / release_price - 1.0) * 100.0, 1)
    low_pct = round((min_low / release_price - 1.0) * 100.0, 1)
    return high_pct, low_pct


def build_vi_event_row(
    raw: Dict[str, object],
    *,
    static_up_rows: Sequence[Dict[str, object]],
    minute_bars: Sequence[Dict[str, object]],
    market_cap_billion: Optional[int],
    market: str,
    name: str,
) -> ViEventRow:
    sym = normalize_symbol(raw.get("mksc_shrn_iscd", raw.get("stck_shrn_iscd", "")))
    trigger_hhmmss = normalize_hhmmss(raw.get("cntg_vi_hour", ""))
    release_hhmmss = normalize_hhmmss(raw.get("vi_cncl_hour", ""))
    trigger_price = _safe_int(raw.get("vi_prc"))
    release_price = estimate_release_price(minute_bars, release_hhmmss, trigger_price)
    high_pct, low_pct = post_release_pct_range(minute_bars, release_hhmmss, release_price)
    return ViEventRow(
        symbol=sym,
        name=name,
        market=market,
        trigger_hhmmss=trigger_hhmmss,
        release_hhmmss=release_hhmmss,
        trigger_price=trigger_price,
        release_price=release_price,
        has_second_vi=has_second_static_upward_vi(sym, static_up_rows),
        post_release_high_pct=high_pct,
        post_release_low_pct=low_pct,
        market_cap_billion=market_cap_billion,
        pre_vi_trading_value=pre_vi_trading_value(minute_bars, trigger_hhmmss),
        cap_group=market_cap_group(market_cap_billion),
    )


def _safe_int(value: object) -> int:
    try:
        if value is None:
            return 0
        text = str(value).replace(",", "").strip()
        if not text:
            return 0
        return int(float(text))
    except Exception:
        return 0
