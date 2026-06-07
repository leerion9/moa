"""Tests for gap_backfill CLI (no HTTP)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from config.settings import settings as real_settings
from scripts import gap_backfill


def _mock_settings(**overrides):
    base = {
        "history_cache_dir": real_settings.history_cache_dir,
        "gap_backfill_dir": real_settings.gap_backfill_dir,
        "gap_backfill_xlsx_path": real_settings.gap_backfill_xlsx_path,
        "gap_backfill_ticks_dir": real_settings.gap_backfill_ticks_dir,
        "holiday_dates_path": real_settings.holiday_dates_path,
        "symbol_master_path": real_settings.symbol_master_path,
        "gap_min_pct": real_settings.gap_min_pct,
        "gap_max_pct": real_settings.gap_max_pct,
        "gap_dip_min_pct": real_settings.gap_dip_min_pct,
        "gap_trailing_stop_pct": real_settings.gap_trailing_stop_pct,
        "gap_buy_qty": real_settings.gap_buy_qty,
        "gap_backfill_batch_size": real_settings.gap_backfill_batch_size,
        "gap_naver_tick_delay_sec": real_settings.gap_naver_tick_delay_sec,
        "fee_rate_buy": real_settings.fee_rate_buy,
        "fee_rate_sell": real_settings.fee_rate_sell,
        "tax_rate_sell": real_settings.tax_rate_sell,
        "naver_http_delay_sec": real_settings.naver_http_delay_sec,
        "symbol_master_max_age_days": real_settings.symbol_master_max_age_days,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_plan_command_local_only(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "history_cache"
    cache_dir.mkdir()
    payload = {
        "symbol": "005930",
        "bars": [
            {
                "date": "2025.06.09",
                "open": 10500,
                "high": 10600,
                "low": 10000,
                "close": 10550,
                "volume": 1000,
            },
            {
                "date": "2025.06.08",
                "open": 10000,
                "high": 10050,
                "low": 9900,
                "close": 10000,
                "volume": 900,
            },
        ],
    }
    import json
    (cache_dir / "005930.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        gap_backfill,
        "settings",
        _mock_settings(
            history_cache_dir=cache_dir,
            gap_backfill_dir=tmp_path / "gap_backfill",
            gap_backfill_xlsx_path=tmp_path / "gap_backfill.xlsx",
            holiday_dates_path=tmp_path / "missing_holidays.txt",
        ),
    )

    rc = gap_backfill.main(["plan", "--year", "2025"])
    assert rc == 0
    queue_file = tmp_path / "gap_backfill" / "queue_2025.json"
    assert queue_file.is_file()


def test_run_dry_run_no_execute(tmp_path: Path, monkeypatch):
    import json
    from core.gap_backfill_queue import BackfillTask, save_queue

    bf_dir = tmp_path / "gap_backfill"
    save_queue(
        bf_dir,
        2025,
        [
            BackfillTask(
                symbol="005930",
                ymd="20250609",
                open_price=10500,
                prev_close=10000,
                gap_pct=5.0,
                close_price=10600,
            )
        ],
    )

    monkeypatch.setattr(
        gap_backfill,
        "settings",
        _mock_settings(
            gap_backfill_dir=bf_dir,
            gap_backfill_xlsx_path=tmp_path / "gap_backfill.xlsx",
            gap_backfill_ticks_dir=tmp_path / "ticks",
            symbol_master_path=tmp_path / "master.json",
        ),
    )
    (tmp_path / "master.json").write_text("{}", encoding="utf-8")

    rc = gap_backfill.main(["run", "--year", "2025", "--limit", "1"])
    assert rc == 0
    assert not (tmp_path / "gap_backfill.xlsx").exists()
