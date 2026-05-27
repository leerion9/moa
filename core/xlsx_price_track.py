"""Shared helpers for n / n+1..n+15 price tracking columns in xlsx files."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import FrozenSet, List, Optional

from openpyxl.styles import Font

TRACK_DAY_COUNT = 15
TRACK_HEADERS: List[str] = ["n"] + [f"n+{k}" for k in range(1, TRACK_DAY_COUNT + 1)]

FONT_POSITIVE = Font(color="C00000")
FONT_NEGATIVE = Font(color="0070C0")
FONT_ZERO = Font(color="000000")


def pct_vs_ref(high: int, ref: int) -> Optional[float]:
    if ref <= 0 or high <= 0:
        return None
    return round((high / ref - 1.0) * 100.0, 1)


def font_for_pct(value: Optional[float]) -> Font:
    if value is None:
        return FONT_ZERO
    if value > 0:
        return FONT_POSITIVE
    if value < 0:
        return FONT_NEGATIVE
    return FONT_ZERO


def is_weekend_ymd(ymd: str) -> bool:
    return datetime.strptime(ymd, "%Y%m%d").weekday() >= 5


def next_trading_day(ymd: str, holidays: FrozenSet[str]) -> str:
    dt = datetime.strptime(ymd, "%Y%m%d")
    while True:
        dt += timedelta(days=1)
        candidate = dt.strftime("%Y%m%d")
        if is_weekend_ymd(candidate) or candidate in holidays:
            continue
        return candidate


def entry_plus_trading_days(entry_ymd: str, k: int, holidays: FrozenSet[str]) -> str:
    current = entry_ymd
    for _ in range(k):
        current = next_trading_day(current, holidays)
    return current


def ymd_to_date(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    text = str(value).strip().replace("-", "").replace("/", "")
    if len(text) >= 8 and text[:8].isdigit():
        return text[:8]
    return None


def normalize_symbol(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.isdigit():
        return text.zfill(6)
    return text


def get_daily_high_from_bars(bars: List[dict], ymd: str) -> Optional[int]:
    for bar in bars:
        if str(bar.get("date", "") or "").strip() == ymd:
            high = int(bar.get("high", 0) or 0)
            return high if high > 0 else None
    return None


def write_pct_cell(cell, value: Optional[float]) -> None:
    if value is None:
        cell.value = None
        cell.font = FONT_ZERO
        return
    cell.value = float(value)
    cell.font = font_for_pct(value)


def write_ref_price_cell(cell, value: Optional[int]) -> None:
    if value is None or value <= 0:
        cell.value = None
        cell.font = FONT_ZERO
        return
    cell.value = int(value)
    cell.font = FONT_ZERO
