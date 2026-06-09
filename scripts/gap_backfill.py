"""
Gap 전략 과거 소급 백필 — 네이버 체결(sise_time) 기반.

실제 HTTP 크롤링은 --execute 없이는 수행하지 않습니다 (dry-run 기본).

사용 예::
    # 1) history_cache 일봉으로 연도별 후보 큐 생성 (네트워크 없음)
    python -m scripts.gap_backfill plan --year 2025

    # 2) 처리 예정 작업 확인 (크롤링 없음)
    python -m scripts.gap_backfill run --year 2025 --limit 10

    # 3) 실제 크롤링 + 시뮬 + gap_backfill.xlsx 기록
    python -m scripts.gap_backfill run --year 2025 --limit 30 --execute

    python -m scripts.gap_backfill status --year 2025
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Set

from config.settings import settings
from core.gap_backfill_queue import (
    BackfillTask,
    done_keys,
    filter_pending_tasks,
    in_date_range,
    load_queue,
    mark_done,
    mark_failed,
    plan_tasks_for_year,
    pop_tasks,
    queue_stats,
    save_queue,
    task_key,
    task_to_candidate,
)
from core.gap_collector_logic import (
    bar_volume_for_ymd,
    build_gap_result_row,
    latest_close_from_bars,
    simulate_gap_trade,
)
from core.naver_universe import fetch_market_cap_list
from core.gap_naver_ticks import (
    fetch_intraday_minute_bars,
    filter_regular_session_bars,
    load_minute_cache,
    load_ticks_cache,
    minute_cache_path,
    save_minute_cache,
    tick_cache_path,
    ticks_to_minute_bars,
)
from core.gap_result_xlsx import append_gap_result_rows, read_existing_trade_keys
from core.history_cache import load_symbol_bars
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.trading_day import load_manual_holiday_set

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | moa | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("moa")


def _list_cached_symbols(cache_dir: Path) -> List[str]:
    if not cache_dir.is_dir():
        return []
    return sorted(
        p.stem
        for p in cache_dir.glob("*.json")
        if p.stem.isdigit() and len(p.stem) == 6
    )


def _load_cache_bars(cache_dir: Path, symbols: List[str]) -> Dict[str, List[Dict[str, object]]]:
    out: Dict[str, List[Dict[str, object]]] = {}
    for sym in symbols:
        data = load_symbol_bars(cache_dir / f"{sym}.json")
        if not data:
            continue
        bars = data.get("bars")
        if isinstance(bars, list) and bars:
            out[sym] = [dict(b) for b in bars if isinstance(b, dict)]
    return out


def _collect_skip_keys(xlsx_path: Path, state_dir: Path) -> Set[str]:
    skip: Set[str] = set(done_keys(state_dir))
    for sym, ymd in read_existing_trade_keys(xlsx_path):
        skip.add(task_key(sym, ymd))
    return skip


def _parse_ymd_arg(value: str | None) -> str:
    text = str(value or "").strip().replace("-", "").replace(".", "")
    if text and (len(text) != 8 or not text.isdigit()):
        raise ValueError(f"날짜 형식 오류: {value} (YYYYMMDD)")
    return text


def cmd_plan(year: int, *, from_ymd: str = "", to_ymd: str = "") -> int:
    symbols = _list_cached_symbols(settings.history_cache_dir)
    if not symbols:
        _log.error("history_cache 비어 있음. bootstrap 후 plan 실행하세요.")
        return 1

    cache_bars = _load_cache_bars(settings.history_cache_dir, symbols)
    holidays = load_manual_holiday_set(settings.holiday_dates_path)
    skip = _collect_skip_keys(settings.gap_backfill_xlsx_path, settings.gap_backfill_dir)

    tasks = plan_tasks_for_year(
        cache_bars,
        year,
        holidays,
        gap_min_pct=settings.gap_min_pct,
        gap_max_pct=settings.gap_max_pct,
        skip_keys=skip,
        from_ymd=from_ymd,
        to_ymd=to_ymd,
    )
    count = save_queue(settings.gap_backfill_dir, year, tasks)
    range_note = ""
    if from_ymd or to_ymd:
        range_note = f" range={from_ymd or '...'}~{to_ymd or '...'}"
    _log.info(
        "plan 완료 year=%d candidates=%d%s queue=%s",
        year,
        count,
        range_note,
        settings.gap_backfill_dir / f"queue_{year}.json",
    )
    return 0


def cmd_status(year: int) -> int:
    stats = queue_stats(settings.gap_backfill_dir, year)
    skip = _collect_skip_keys(settings.gap_backfill_xlsx_path, settings.gap_backfill_dir)
    _log.info(
        "status year=%d queued=%d done=%d pending=%d skip_keys=%d xlsx=%s",
        year,
        stats["queued"],
        stats["done"],
        stats["pending"],
        len(skip),
        settings.gap_backfill_xlsx_path,
    )
    return 0


def _resolve_minute_bars(task: BackfillTask, *, execute: bool) -> List[Dict[str, object]]:
    minute_file = minute_cache_path(
        settings.gap_backfill_ticks_dir,
        task.ymd,
        task.symbol,
    )
    cached = load_minute_cache(minute_file)
    if cached:
        return cached

    tick_file = tick_cache_path(
        settings.gap_backfill_ticks_dir,
        task.ymd,
        task.symbol,
    )
    legacy_ticks = load_ticks_cache(tick_file)
    if legacy_ticks:
        return ticks_to_minute_bars(legacy_ticks)

    if not execute:
        return []

    bars = fetch_intraday_minute_bars(
        task.symbol,
        task.ymd,
        delay_sec=settings.gap_naver_tick_delay_sec,
    )
    if bars:
        save_minute_cache(
            minute_file,
            symbol=task.symbol,
            ymd=task.ymd,
            bars=bars,
            source="fchart_or_ticks",
        )
    return bars


def _process_task(
    task: BackfillTask,
    *,
    execute: bool,
    names: Dict[str, str],
    caps: Dict[str, int],
    cache_bars: Dict[str, List[Dict[str, object]]],
) -> Dict[str, object] | None:
    minute_bars = filter_regular_session_bars(_resolve_minute_bars(task, execute=execute))
    if not minute_bars:
        if execute:
            _log.warning("  분봉 데이터 없음 %s %s", task.symbol, task.ymd)
        else:
            _log.info(
                "  [dry-run] %s %s gap=%.1f%% open=%d (크롤링/캐시 없음)",
                task.symbol,
                task.ymd,
                task.gap_pct,
                task.open_price,
            )
        return None
    cand = task_to_candidate(task)
    trade = simulate_gap_trade(
        cand.open_price,
        minute_bars,
        dip_min_pct=settings.gap_dip_min_pct,
        trailing_stop_pct=settings.gap_trailing_stop_pct,
        qty=settings.gap_buy_qty,
        close_price=cand.close_price,
    )
    if trade is None:
        _log.info("  시뮬 신호 없음 %s %s", task.symbol, task.ymd)
        return None

    symbol_bars = cache_bars.get(task.symbol, [])
    return build_gap_result_row(
        cand,
        trade,
        buy_ymd=task.ymd,
        name=names.get(task.symbol, ""),
        fee_rate_buy=settings.fee_rate_buy,
        fee_rate_sell=settings.fee_rate_sell,
        tax_rate_sell=settings.tax_rate_sell,
        daily_volume=bar_volume_for_ymd(symbol_bars, task.ymd),
        current_price=latest_close_from_bars(symbol_bars),
        market_cap_billion=caps.get(task.symbol),
    )


def cmd_run(
    year: int,
    *,
    limit: int,
    execute: bool,
    from_ymd: str = "",
    to_ymd: str = "",
) -> int:
    queue = load_queue(settings.gap_backfill_dir, year)
    if not queue:
        _log.error("queue 비어 있음. 먼저: python -m scripts.gap_backfill plan --year %d", year)
        return 1

    skip = _collect_skip_keys(settings.gap_backfill_xlsx_path, settings.gap_backfill_dir)
    pending = filter_pending_tasks(queue, skip_keys=skip)
    if from_ymd or to_ymd:
        pending = [
            t for t in pending if in_date_range(t.ymd, from_ymd=from_ymd, to_ymd=to_ymd)
        ]
    batch, _rest = pop_tasks(pending, limit)

    if not batch:
        _log.info("run: 처리할 pending 작업 없음 year=%d", year)
        return 0

    mode = "execute" if execute else "dry-run"
    _log.info("run 시작 year=%d mode=%s batch=%d/%d pending", year, mode, len(batch), len(pending))

    names = load_or_refresh_symbol_master(
        settings.symbol_master_path,
        auto_refresh=False,
        max_age_days=settings.symbol_master_max_age_days,
        delay_sec=settings.naver_http_delay_sec,
    )

    caps: Dict[str, int] = {}
    cache_bars: Dict[str, List[Dict[str, object]]] = {}
    if execute:
        cap_list, cap_names = fetch_market_cap_list(delay_sec=settings.naver_http_delay_sec)
        caps = {sym: cap for sym, cap in cap_list}
        for sym, cap_name in cap_names.items():
            if sym not in names and cap_name:
                names[sym] = cap_name
        symbols = sorted({task.symbol for task in batch})
        cache_bars = _load_cache_bars(settings.history_cache_dir, symbols)

    result_rows: List[Dict[str, object]] = []
    for i, task in enumerate(batch, 1):
        _log.info("작업 %d/%d %s", i, len(batch), task.key)
        try:
            row = _process_task(
                task,
                execute=execute,
                names=names,
                caps=caps,
                cache_bars=cache_bars,
            )
        except Exception as exc:
            _log.error("  실패 %s: %s", task.key, exc)
            if execute:
                mark_failed(settings.gap_backfill_dir, task.key, str(exc))
            continue

        if execute:
            mark_done(settings.gap_backfill_dir, task.key)
            if row is not None:
                result_rows.append(row)

    if execute and result_rows:
        written = append_gap_result_rows(
            settings.gap_backfill_xlsx_path,
            result_rows,
            include_market_fields=False,
        )
        _log.info("xlsx 기록 %d건 -> %s", written, settings.gap_backfill_xlsx_path)
    elif not execute:
        _log.info("dry-run 완료. 실제 크롤링하려면 --execute 추가")

    _log.info(
        "run 완료 year=%d processed=%d signals=%d",
        year,
        len(batch),
        len(result_rows),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gap strategy historical backfill (Naver ticks)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="history_cache로 연도별 후보 큐 생성 (HTTP 없음)")
    p_plan.add_argument("--year", type=int, required=True, help="예: 2025")
    p_plan.add_argument("--from", dest="from_ymd", help="시작일 YYYYMMDD")
    p_plan.add_argument("--to", dest="to_ymd", help="종료일 YYYYMMDD")

    p_run = sub.add_parser("run", help="큐에서 N건 처리 (기본 dry-run)")
    p_run.add_argument("--year", type=int, required=True)
    p_run.add_argument("--from", dest="from_ymd", help="시작일 YYYYMMDD")
    p_run.add_argument("--to", dest="to_ymd", help="종료일 YYYYMMDD")
    p_run.add_argument(
        "--limit",
        type=int,
        default=0,
        help="처리 건수 (0=GAP_BACKFILL_BATCH_SIZE)",
    )
    p_run.add_argument(
        "--execute",
        action="store_true",
        help="실제 네이버 크롤링 수행 (없으면 dry-run)",
    )

    p_status = sub.add_parser("status", help="큐/진행 상태 출력")
    p_status.add_argument("--year", type=int, required=True)

    args = parser.parse_args(argv)

    try:
        from_ymd = _parse_ymd_arg(getattr(args, "from_ymd", None))
        to_ymd = _parse_ymd_arg(getattr(args, "to_ymd", None))
    except ValueError as exc:
        _log.error("%s", exc)
        return 1

    if args.command == "plan":
        return cmd_plan(args.year, from_ymd=from_ymd, to_ymd=to_ymd)
    if args.command == "status":
        return cmd_status(args.year)
    if args.command == "run":
        limit = args.limit or settings.gap_backfill_batch_size
        return cmd_run(
            args.year,
            limit=limit,
            execute=args.execute,
            from_ymd=from_ymd,
            to_ymd=to_ymd,
        )

    return 1


if __name__ == "__main__":
    sys.exit(main())
