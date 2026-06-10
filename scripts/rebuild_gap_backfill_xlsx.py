"""
Rebuild gap_backfill.xlsx from local minute caches only (no Naver HTTP).

Uses regular-session filter (09:00-15:30) and close sell at 15:30.
Only (date, symbol) pairs with *_minute.json under data/gap_backfill/ticks/ are included.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

from config.settings import settings
from core.gap_backfill_queue import BackfillTask, load_queue, task_key
from core.gap_collector_logic import find_gap_candidate
from core.gap_naver_ticks import minute_cache_path
from core.gap_result_xlsx import append_gap_result_rows
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.naver_universe import fetch_market_cap_list
from core.trading_day import load_manual_holiday_set
from scripts.gap_backfill import _load_cache_bars, _process_task

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | moa | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("moa")


def _cached_symbol_dates(ticks_dir: Path) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    if not ticks_dir.is_dir():
        return out
    for path in sorted(ticks_dir.rglob("*_minute.json")):
        ymd = path.parent.name
        sym = path.name.replace("_minute.json", "")
        if len(ymd) == 8 and ymd.isdigit() and len(sym) == 6:
            out.append((ymd, sym))
    return out


def _task_map(year: int) -> Dict[str, BackfillTask]:
    queue = load_queue(settings.gap_backfill_dir, year)
    return {t.key: t for t in queue}


def _parse_ymd(value: str) -> str:
    text = str(value or "").strip().replace("-", "").replace(".", "")
    if text and (len(text) != 8 or not text.isdigit()):
        raise ValueError(f"날짜 형식 오류: {value} (YYYYMMDD)")
    return text


def _task_from_cache(
    sym: str,
    ymd: str,
    cache_bars: Dict[str, List[Dict[str, object]]],
) -> BackfillTask | None:
    bars = cache_bars.get(sym)
    if not bars:
        return None
    tagged = [{**b, "symbol": sym} for b in bars]
    cand = find_gap_candidate(
        tagged,
        ymd,
        gap_min_pct=settings.gap_min_pct,
        gap_max_pct=settings.gap_max_pct,
    )
    if cand is None:
        return None
    return BackfillTask(
        symbol=sym,
        ymd=ymd,
        open_price=cand.open_price,
        prev_close=cand.prev_close,
        gap_pct=cand.gap_pct,
        close_price=cand.close_price,
    )


def rebuild(
    *,
    xlsx_path: Path,
    year: int,
    batch_size: int = 100,
    from_ymd: str = "",
    to_ymd: str = "",
) -> int:
    pairs = _cached_symbol_dates(settings.gap_backfill_ticks_dir)
    if not pairs:
        _log.error("minute cache 없음: %s", settings.gap_backfill_ticks_dir)
        return 1

    from_ymd = _parse_ymd(from_ymd)
    to_ymd = _parse_ymd(to_ymd)

    filtered_pairs = [
        (ymd, sym)
        for ymd, sym in pairs
        if (not from_ymd or ymd >= from_ymd) and (not to_ymd or ymd <= to_ymd)
    ]
    symbols = sorted({sym for _, sym in filtered_pairs})
    cache_bars = _load_cache_bars(settings.history_cache_dir, symbols)
    tasks_by_key = _task_map(year)
    missing = 0
    cache_fallback = 0
    work: List[BackfillTask] = []
    for ymd, sym in filtered_pairs:
        if not minute_cache_path(settings.gap_backfill_ticks_dir, ymd, sym).is_file():
            continue
        key = task_key(sym, ymd)
        task = tasks_by_key.get(key)
        if task is None:
            task = _task_from_cache(sym, ymd, cache_bars)
            if task is None:
                missing += 1
                continue
            cache_fallback += 1
        work.append(task)

    dates = sorted({t.ymd for t in work})
    _log.info(
        "rebuild 대상: %d건 (%d dates), cache fallback %d건, skip %d건",
        len(work),
        len(dates),
        cache_fallback,
        missing,
    )
    _log.info("dates: %s", ", ".join(dates))

    out_path = xlsx_path
    if xlsx_path.is_file():
        try:
            xlsx_path.unlink()
            _log.info("기존 xlsx 삭제: %s", xlsx_path)
        except OSError as exc:
            out_path = xlsx_path.with_name(f"{xlsx_path.stem}_rebuilt{xlsx_path.suffix}")
            _log.warning(
                "xlsx 잠김 -> %s 로 저장 후 교체 시도 (%s)",
                out_path,
                exc,
            )

    names = load_or_refresh_symbol_master(
        settings.symbol_master_path,
        auto_refresh=False,
        max_age_days=settings.symbol_master_max_age_days,
        delay_sec=settings.naver_http_delay_sec,
    )
    cap_list, cap_names = fetch_market_cap_list(delay_sec=settings.naver_http_delay_sec)
    caps = {sym: cap for sym, cap in cap_list}
    for sym, cap_name in cap_names.items():
        if sym not in names and cap_name:
            names[sym] = cap_name

    symbols = sorted({t.symbol for t in work})
    if not cache_bars:
        cache_bars = _load_cache_bars(settings.history_cache_dir, symbols)
    holidays = load_manual_holiday_set(settings.holiday_dates_path)

    total_rows = 0
    batch: List[Dict[str, object]] = []
    for i, task in enumerate(work, 1):
        row = _process_task(
            task,
            execute=False,
            names=names,
            caps=caps,
            cache_bars=cache_bars,
            holidays=holidays,
        )
        if row is not None:
            batch.append(row)
        if len(batch) >= batch_size:
            total_rows += append_gap_result_rows(
                out_path, batch, include_market_fields=False
            )
            batch.clear()
            _log.info("진행 %d/%d xlsx_rows=%d", i, len(work), total_rows)

    if batch:
        total_rows += append_gap_result_rows(
            out_path, batch, include_market_fields=False
        )

    if out_path != xlsx_path and out_path.is_file():
        try:
            if xlsx_path.is_file():
                xlsx_path.unlink()
            out_path.replace(xlsx_path)
            out_path = xlsx_path
            _log.info("xlsx 교체 완료: %s", xlsx_path)
        except OSError as exc:
            _log.warning("xlsx 교체 실패 — %s 사용 (%s)", out_path, exc)

    _log.info("rebuild 완료 rows=%d path=%s", total_rows, out_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild gap_backfill.xlsx from minute caches")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--from", dest="from_ymd", help="시작일 YYYYMMDD")
    parser.add_argument("--to", dest="to_ymd", help="종료일 YYYYMMDD")
    args = parser.parse_args(argv)
    try:
        return rebuild(
            xlsx_path=settings.gap_backfill_xlsx_path,
            year=args.year,
            from_ymd=args.from_ymd or "",
            to_ymd=args.to_ymd or "",
        )
    except ValueError as exc:
        _log.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
