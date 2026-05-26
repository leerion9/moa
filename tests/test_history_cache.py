"""Tests for core/history_cache.py."""

from __future__ import annotations

from pathlib import Path

from core.history_cache import (
    compute_w52_from_bars,
    is_full_cache,
    load_symbol_bars,
    merge_bars,
    save_symbol_bars,
    symbol_cache_path,
)


def test_merge_bars_dedup_and_newest_first():
    existing = [
        {"date": "20260520", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
        {"date": "20260519", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
    ]
    incoming = [
        {"date": "20260521", "open": 3, "high": 4, "low": 3, "close": 4, "volume": 200},
        {"date": "20260520", "open": 9, "high": 9, "low": 9, "close": 9, "volume": 999},
    ]
    merged = merge_bars(existing, incoming)
    assert [b["date"] for b in merged] == ["20260521", "20260520", "20260519"]
    assert merged[1]["close"] == 9


def test_compute_w52_from_bars():
    bars = [
        {"date": "20260502", "high": 120, "low": 100, "close": 110, "volume": 1},
        {"date": "20260501", "high": 150, "low": 90, "close": 100, "volume": 1},
    ]
    high, low = compute_w52_from_bars(bars)
    assert high == 150
    assert low == 90


def test_save_and_load_symbol_bars(tmp_path: Path):
    sym = "005930"
    path = symbol_cache_path(tmp_path, sym)
    bars = [
        {"date": "20260501", "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10},
    ]
    save_symbol_bars(path, sym, bars, full_pages=0)
    data = load_symbol_bars(path)
    assert data is not None
    assert is_full_cache(data) is False
    assert len(data["bars"]) == 1


def test_is_full_cache_by_bar_count(tmp_path: Path):
    sym = "000660"
    path = symbol_cache_path(tmp_path, sym)
    bars = [
        {
            "date": f"2026{(i // 28 + 1):02d}{(i % 28 + 1):02d}",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 10,
        }
        for i in range(130)
    ]
    save_symbol_bars(path, sym, bars, full_pages=0)
    assert is_full_cache(load_symbol_bars(path)) is True
