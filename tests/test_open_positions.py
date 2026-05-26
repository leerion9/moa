"""Tests for core/open_positions.py and held-symbol buy exclusion."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.open_positions import (
    load_open_positions_from_trades_csv,
    open_positions_from_execs,
)
from core.result_csv import Exec
from core.strategy import W52HighStrategy

KST = ZoneInfo("Asia/Seoul")
TRAILING = 0.075


def _write_trades_csv(path: Path, rows: list[dict]) -> None:
    fields = ["ts", "symbol", "side", "qty", "price", "reason", "order_id"]
    with path.open("w", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def test_open_positions_from_execs_partial_sell():
    execs = [
        Exec(
            ts=datetime(2026, 5, 25, 10, 0, tzinfo=KST),
            side="BUY",
            symbol="005930",
            qty=10,
            amount=700_000.0,
            fee=0.0,
            tax=0.0,
            avg_px=70_000.0,
        ),
        Exec(
            ts=datetime(2026, 5, 26, 11, 0, tzinfo=KST),
            side="SELL",
            symbol="005930",
            qty=4,
            amount=300_000.0,
            fee=0.0,
            tax=0.0,
            avg_px=75_000.0,
        ),
    ]
    pos = open_positions_from_execs(execs)
    assert "005930" in pos
    assert pos["005930"].qty == 6
    assert pos["005930"].avg_buy_price == 70_000


def test_load_open_positions_from_trades_csv(tmp_path: Path):
    path = tmp_path / "trades.csv"
    _write_trades_csv(
        path,
        [
            {
                "ts": "2026-05-25T10:00:00+09:00",
                "symbol": "380540",
                "side": "BUY",
                "qty": "1",
                "price": "5550",
                "reason": "w52_high_breakout",
                "order_id": "SIM",
            },
        ],
    )
    pos = load_open_positions_from_trades_csv(path)
    assert pos["380540"].qty == 1
    assert pos["380540"].avg_buy_price == 5550


def test_apply_open_position_excludes_from_buy_watchlist():
    s = W52HighStrategy(trailing_stop_pct=TRAILING)
    s.register("005930", w52_high=70000, vol_ma20=1000)
    s.register("000660", w52_high=80000, vol_ma20=2000)

    s.apply_open_position("005930", buy_price=69000, qty=5, w52_high=70000, vol_ma20=1000)

    assert "005930" not in s.watchlist_symbols()
    assert "000660" in s.watchlist_symbols()
    assert s.on_quote("005930", current_price=70000, current_volume=1_000_000) is None
    assert "005930" in s.held_symbols()
