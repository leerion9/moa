"""
장마감 후 갭상승 회복 전략 시뮬 수집 -> gap_result.xlsx append.

사용 예::
    python -m scripts.gap_collector
    python -m scripts.gap_collector --date 20260609

Windows 작업 스케줄러 (15:35~15:40):
    python -m scripts.gap_collector

같은 실행에서 gap_result.xlsx(KIS) 기록 후 gap_backfill.xlsx(Naver) 당일 백필도 자동 수행.
백필만 생략: --skip-backfill
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
from zoneinfo import ZoneInfo

import requests

from config.settings import settings
from core.api_client import KISApiClient
from core.gap_collector_logic import (
    GapCandidate,
    GapTradeResult,
    bar_volume_for_ymd,
    build_gap_result_row,
    calc_trade_amounts,
    latest_close_from_bars,
    scan_gap_candidates_from_cache,
    simulate_gap_trade,
)
from core.gap_result_xlsx import append_gap_result_rows, read_existing_buy_dates
from scripts.gap_backfill import backfill_for_date
from core.history_cache import HistoryCacheStore, load_symbol_bars
from core.naver_universe import _MARKET_SUM_URL, _MAX_PAGES_PER_MARKET, _parse_market_sum_page
from core.trading_day import load_manual_holiday_set, should_run_bot_today_kst
from core.vi_collector_logic import trading_value_to_billion_won

KST = ZoneInfo("Asia/Seoul")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | moa | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_log = logging.getLogger("moa")


def _today_kst_yyyymmdd(now: datetime | None = None) -> str:
    dt = now or datetime.now(KST)
    return dt.strftime("%Y%m%d")


def _load_market_cap_table(delay_sec: float) -> Tuple[Dict[str, int], Dict[str, str]]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        }
    )
    caps: Dict[str, int] = {}
    names: Dict[str, str] = {}
    for sosok in (0, 1):
        for page in range(1, _MAX_PAGES_PER_MARKET + 1):
            try:
                resp = session.get(
                    _MARKET_SUM_URL,
                    params={"sosok": sosok, "page": page},
                    timeout=15,
                )
                resp.encoding = "euc-kr"
                resp.raise_for_status()
                rows = _parse_market_sum_page(resp.text)
                if not rows:
                    break
                for code, cap, name in rows:
                    caps[code] = max(caps.get(code, 0), cap)
                    if name and code not in names:
                        names[code] = name
                time.sleep(delay_sec)
            except Exception as exc:
                _log.warning("Naver cap page failed sosok=%s page=%s: %s", sosok, page, exc)
                break
    _log.info("Market cap table loaded: %d symbols", len(caps))
    return caps, names


def _list_cached_symbols(cache_dir: Path) -> List[str]:
    if not cache_dir.is_dir():
        return []
    out: List[str] = []
    for path in cache_dir.glob("*.json"):
        stem = path.stem
        if stem.isdigit() and len(stem) == 6:
            out.append(stem)
    return sorted(out)


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


def _trade_to_row(
    date_yyyymmdd: str,
    cand: GapCandidate,
    trade: GapTradeResult,
    *,
    name: str,
    market_cap_billion: int | None,
    symbol_bars: List[Dict[str, object]],
) -> Dict[str, object]:
    return build_gap_result_row(
        cand,
        trade,
        buy_ymd=date_yyyymmdd,
        name=name,
        fee_rate_buy=settings.fee_rate_buy,
        fee_rate_sell=settings.fee_rate_sell,
        tax_rate_sell=settings.tax_rate_sell,
        market_cap_billion=market_cap_billion,
        trading_value_won=trade.trading_value_won,
        trading_value_billion=trading_value_to_billion_won(trade.trading_value_won),
        daily_volume=bar_volume_for_ymd(symbol_bars, date_yyyymmdd),
        current_price=latest_close_from_bars(symbol_bars),
        symbol_bars=symbol_bars,
        holidays=load_manual_holiday_set(settings.holiday_dates_path),
    )


def _simulate_candidate(
    api: KISApiClient,
    cand: GapCandidate,
    *,
    index: int,
    total: int,
) -> Dict[str, object] | None:
    sym = cand.symbol
    _log.info("분봉 조회 %d/%d %s gap=%.1f%%", index, total, sym, cand.gap_pct)
    try:
        minute_bars = api.get_intraday_minute_bars(sym)
    except Exception as exc:
        _log.error("분봉 실패 %s: %s", sym, exc)
        minute_bars = []

    trade = simulate_gap_trade(
        cand.open_price,
        minute_bars,
        dip_min_pct=settings.gap_dip_min_pct,
        trailing_stop_pct=settings.gap_trailing_stop_pct,
        qty=settings.gap_buy_qty,
        close_price=cand.close_price,
    )
    if trade is None:
        _log.info("  시뮬 신호 없음 %s (하락/회복 미충족)", sym)
        return None
    return {"cand": cand, "trade": trade}


def collect_gap_trades_for_date(
    api: KISApiClient,
    date_yyyymmdd: str,
    *,
    cap_table: Dict[str, int] | None = None,
    name_table: Dict[str, str] | None = None,
    skip_history_update: bool = False,
) -> List[Dict[str, object]]:
    _log.info("Gap 수집 시작 date=%s", date_yyyymmdd)

    symbols = _list_cached_symbols(settings.history_cache_dir)
    if not symbols:
        _log.warning("history_cache 비어 있음. bootstrap 후 재실행 권장.")
        return []

    if not skip_history_update:
        store = HistoryCacheStore(
            settings.history_cache_dir,
            delay_sec=settings.naver_http_delay_sec,
            jitter_sec=settings.naver_request_jitter_sec,
            batch_size=settings.naver_batch_size,
            batch_pause_sec=settings.naver_batch_pause_sec,
            api=api,
        )
        _log.info("history_cache 증분 갱신 시작 (%d symbols)...", len(symbols))
        hc_stats = store.update_all(symbols)
        _log.info(
            "history_cache 완료 bootstrap=%d updated=%d skipped=%d failed=%d",
            hc_stats.bootstrapped,
            hc_stats.updated,
            hc_stats.skipped,
            hc_stats.failed,
        )

    cache_bars = _load_cache_bars(settings.history_cache_dir, symbols)
    candidates = scan_gap_candidates_from_cache(
        cache_bars,
        date_yyyymmdd,
        gap_min_pct=settings.gap_min_pct,
        gap_max_pct=settings.gap_max_pct,
    )
    _log.info(
        "갭 %.1f~%.1f%% 후보: %d종목",
        settings.gap_min_pct,
        settings.gap_max_pct,
        len(candidates),
    )

    caps = cap_table or {}
    names = name_table or {}
    rows: List[Dict[str, object]] = []

    for i, cand in enumerate(candidates, 1):
        result = _simulate_candidate(api, cand, index=i, total=len(candidates))
        if result is None:
            continue
        trade = result["trade"]
        amounts = calc_trade_amounts(
            trade.buy_price,
            trade.sell_price,
            trade.qty,
            fee_rate_buy=settings.fee_rate_buy,
            fee_rate_sell=settings.fee_rate_sell,
            tax_rate_sell=settings.tax_rate_sell,
        )
        rows.append(
            _trade_to_row(
                date_yyyymmdd,
                cand,
                trade,
                name=names.get(cand.symbol, ""),
                market_cap_billion=caps.get(cand.symbol),
                symbol_bars=cache_bars.get(cand.symbol, []),
            )
        )
        _log.info(
            "  신호 %s buy=%s sell=%s pnl=%.0f (%.2f%%) reason=%s",
            cand.symbol,
            trade.buy_hhmmss,
            trade.sell_hhmmss,
            amounts["pnl"],
            amounts["return_pct"],
            trade.sell_reason,
        )
        time.sleep(0.05)

    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gap recovery strategy EOD collector")
    parser.add_argument("--date", help="YYYYMMDD (default: today KST)")
    parser.add_argument("--force", action="store_true", help="같은 매수날짜가 있어도 append")
    parser.add_argument("--skip-history-update", action="store_true", help="history_cache 증분 생략")
    parser.add_argument(
        "--skip-backfill",
        action="store_true",
        help="gap_backfill.xlsx 당일 Naver 백필 생략",
    )
    args = parser.parse_args(argv)

    now = datetime.now(KST)
    date_kst = args.date or _today_kst_yyyymmdd(now)

    ok, reason = should_run_bot_today_kst(date_kst, settings)
    if not ok:
        _log.info("Gap 수집 스킵: %s (%s)", date_kst, reason)
        return 0

    xlsx_path = settings.gap_result_xlsx_path
    gap_result_skipped = (
        not args.force and date_kst in read_existing_buy_dates(xlsx_path)
    )
    if gap_result_skipped:
        _log.info("Gap 수집 스킵: %s 이미 gap_result.xlsx에 기록됨", date_kst)
    else:
        try:
            settings.validate()
        except ValueError as exc:
            _log.error("설정 오류: %s", exc)
            return 1

        cache_path = settings.kis_token_cache_path
        _log.info(
            "KIS token cache: path=%s exists=%s",
            cache_path,
            cache_path.is_file(),
        )

        api = KISApiClient(settings)
        try:
            api.ensure_token()
        except Exception as exc:
            _log.error("KIS token 발급/로드 실패: %s", exc)
            return 1

        caps, names = _load_market_cap_table(settings.naver_http_delay_sec)

        try:
            rows = collect_gap_trades_for_date(
                api,
                date_kst,
                cap_table=caps,
                name_table=names,
                skip_history_update=args.skip_history_update,
            )
        except Exception as exc:
            _log.error("Gap 수집 실패: %s", exc)
            return 1

        written = append_gap_result_rows(
            xlsx_path,
            rows,
            include_market_fields=True,
        )
        _log.info(
            "Gap 수집 완료 date=%s signals=%d written=%d path=%s",
            date_kst,
            len(rows),
            written,
            xlsx_path,
        )

    if args.skip_backfill:
        _log.info("Backfill 생략 (--skip-backfill)")
        return 0

    try:
        settings.validate()
    except ValueError as exc:
        _log.error("설정 오류: %s", exc)
        return 1

    try:
        bf_rc = backfill_for_date(date_kst, force=args.force)
    except Exception as exc:
        _log.error("Backfill 실패: %s", exc)
        return 1
    return bf_rc


if __name__ == "__main__":
    sys.exit(main())
