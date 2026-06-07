"""Tests for gap backfill queue planning."""

from __future__ import annotations

from pathlib import Path

from core.gap_backfill_queue import (
    BackfillTask,
    done_keys,
    mark_done,
    plan_tasks_for_year,
    pop_tasks,
    save_queue,
    task_key,
    trading_days_in_year,
)


def _bars(open_px: int, prev_close: int, *, date_dot: str, close: int = 0):
    return [
        {
            "date": date_dot,
            "open": open_px,
            "high": open_px + 100,
            "low": open_px - 500,
            "close": close or open_px,
            "volume": 10000,
        },
        {
            "date": "2025.06.08",
            "open": prev_close,
            "high": prev_close,
            "low": prev_close,
            "close": prev_close,
            "volume": 8000,
        },
    ]


def test_trading_days_in_year_excludes_weekend():
    days = trading_days_in_year(2025, frozenset())
    assert "20250104" not in days  # Saturday
    assert "20250105" not in days  # Sunday
    assert "20250106" in days  # Monday


def test_plan_tasks_for_year_gap_filter():
    cache = {
        "005930": _bars(10500, 10000, date_dot="2025.06.09"),
        "000660": _bars(10200, 10000, date_dot="2025.06.09"),
    }
    tasks = plan_tasks_for_year(
        cache,
        2025,
        frozenset(),
        gap_min_pct=3.0,
        gap_max_pct=9.0,
        skip_keys=set(),
    )
    assert len(tasks) == 1
    assert tasks[0].symbol == "005930"
    assert tasks[0].ymd == "20250609"
    assert tasks[0].gap_pct == 5.0


def test_queue_state_roundtrip(tmp_path: Path):
    tasks = [
        BackfillTask(
            symbol="005930",
            ymd="20250609",
            open_price=10500,
            prev_close=10000,
            gap_pct=5.0,
            close_price=10600,
        )
    ]
    assert save_queue(tmp_path, 2025, tasks) == 1
    mark_done(tmp_path, task_key("005930", "20250609"))
    assert task_key("005930", "20250609") in done_keys(tmp_path)


def test_pop_tasks():
    tasks = [
        BackfillTask("A", "20250101", 1, 1, 1.0, 1),
        BackfillTask("B", "20250102", 1, 1, 1.0, 1),
    ]
    batch, rest = pop_tasks(tasks, 1)
    assert len(batch) == 1
    assert len(rest) == 1
    assert batch[0].symbol == "A"
