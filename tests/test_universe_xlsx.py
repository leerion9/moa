"""Tests for core/universe_xlsx.py and dual universe row builder."""

from __future__ import annotations

from pathlib import Path

from core.api_client import SymbolHistory
from core.universe_builder import build_universe_xlsx_rows
from core.universe_cache import CachedSymbol
from core.universe_xlsx import append_universe_xlsx_rows, read_max_no


def test_build_universe_xlsx_rows():
    feat1 = CachedSymbol(w52_high=1000, vol_ma20=100, w52_hit_60d=0, w52_hit_10d=0)
    feat2 = CachedSymbol(w52_high=2000, vol_ma20=200, w52_hit_60d=3, w52_hit_10d=6)
    hist = SymbolHistory(
        symbol="005930",
        w52_high=1000,
        w52_low=900,
        bars=[{"date": "20260526", "open": 1, "high": 2, "low": 1, "close": 990, "volume": 1}],
    )
    rows = build_universe_xlsx_rows(
        date_kst="20260526",
        created_at_iso="2026-05-26T08:40:00+09:00",
        cap_by_symbol={"005930": 500000, "000660": 800000},
        symbol_returns={"005930": 0.25, "000660": 0.40},
        rs_passed=["005930", "000660"],
        rs_ranks={"005930": 2, "000660": 1},
        rs_top_pct=0.10,
        features={"005930": feat1, "000660": feat2},
        final_s1={"005930": feat1},
        final_s2={"000660": feat2},
        histories={"005930": hist, "000660": hist},
        symbol_names={"005930": "삼성전자", "000660": "SK하이닉스"},
    )
    assert len(rows) == 2
    s1 = next(r for r in rows if r["strategy_mode"] == 1)
    assert s1["symbol"] == "005930"
    assert s1["name"] == "삼성전자"
    assert s1["rs_rank"] == 2


def test_append_universe_xlsx_rows(tmp_path: Path):
    path = tmp_path / "universe.xlsx"
    rows = [
        {
            "date_kst": "20260526",
            "strategy_mode": 1,
            "symbol": "005930",
            "name": "삼성전자",
            "market_cap_billion": 500000,
            "rs_return_pct": 25.0,
            "rs_rank": 1,
            "rs_top_pct": 0.10,
            "w52_high": 80000,
            "vol_ma20": 1000,
            "w52_hit_60d": 0,
            "w52_hit_10d": 0,
            "close": 79000,
            "bar_count": 300,
            "rs_passed": "Y",
            "watchlisted": "Y",
            "created_at_iso": "2026-05-26T08:40:00+09:00",
        }
    ]
    append_universe_xlsx_rows(path, rows)
    assert path.exists()
    assert read_max_no(path) == 1
    append_universe_xlsx_rows(path, rows)
    assert read_max_no(path) == 2
