"""
장마감 후 정적 상승 VI 이벤트 수집 → vi_universe.xlsx append.

사용 예::
    python -m scripts.vi_collector
    python -m scripts.vi_collector --date 20260528

Windows 작업 스케줄러 (15:35~15:40):
    프로그램: python
    인수: -m scripts.vi_collector
    시작 위치: C:\\cursor\\03_moa
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.naver_universe import _MARKET_SUM_URL, _MAX_PAGES_PER_MARKET, _parse_market_sum_page
from core.trading_day import load_manual_holiday_set, should_run_bot_today_kst
from core.vi_collector_logic import (
    build_vi_event_row,
    select_static_upward_first_vi,
)
from core.vi_universe_xlsx import append_vi_universe_xlsx_rows, read_existing_dates

import requests

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


def _load_market_cap_table(delay_sec: float) -> Tuple[Dict[str, int], Dict[str, str], Dict[str, str]]:
    """Naver 시총 페이지에서 code -> cap(억), name, KOSPI|KOSDAQ."""
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
    markets: Dict[str, str] = {}
    for sosok, market_label in ((0, "KOSPI"), (1, "KOSDAQ")):
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
                    markets[code] = market_label
                time.sleep(delay_sec)
            except Exception as exc:
                _log.warning("Naver cap page load failed sosok=%s page=%s: %s", sosok, page, exc)
                break
    _log.info("Market cap table loaded: %d symbols", len(caps))
    return caps, names, markets


def collect_vi_universe_for_date(
    api: KISApiClient,
    date_yyyymmdd: str,
    *,
    cap_table: Dict[str, int] | None = None,
    name_table: Dict[str, str] | None = None,
    market_table: Dict[str, str] | None = None,
) -> list:
    _log.info("VI 수집 시작 date=%s", date_yyyymmdd)

    dynamic_rows = api.inquire_vi_status_all(
        date_yyyymmdd,
        div_cls_code="0",
        rank_sort_cls_code="2",
    )
    static_up_rows = api.inquire_vi_status_all(
        date_yyyymmdd,
        div_cls_code="1",
        rank_sort_cls_code="1",
    )
    _log.info(
        "KIS VI raw: dynamic=%d static_up=%d",
        len(dynamic_rows),
        len(static_up_rows),
    )

    selected = select_static_upward_first_vi(static_up_rows, dynamic_rows)
    _log.info("1차 정적 상승 VI 후보: %d종목", len(selected))

    caps = cap_table or {}
    names = name_table or {}
    markets = market_table or {}

    events = []
    for i, raw in enumerate(selected, 1):
        sym = str(raw.get("mksc_shrn_iscd", raw.get("stck_shrn_iscd", "")) or "").strip().zfill(6)
        _log.info("분봉 조회 %d/%d %s", i, len(selected), sym)
        try:
            minute_bars = api.get_intraday_minute_bars(sym)
        except Exception as exc:
            _log.error("분봉 실패 %s: %s", sym, exc)
            minute_bars = []
        name = str(raw.get("hts_kor_isnm", "") or names.get(sym, "") or "").strip()
        event = build_vi_event_row(
            raw,
            static_up_rows=static_up_rows,
            minute_bars=minute_bars,
            market_cap_billion=caps.get(sym),
            market=markets.get(sym, ""),
            name=name,
        )
        events.append(event)
        time.sleep(0.05)

    return events


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VI universe batch collector (EOD)")
    parser.add_argument("--date", help="YYYYMMDD (default: today KST)")
    parser.add_argument("--force", action="store_true", help="같은 날짜가 있어도 append")
    args = parser.parse_args(argv)

    now = datetime.now(KST)
    date_kst = args.date or _today_kst_yyyymmdd(now)

    ok, reason = should_run_bot_today_kst(date_kst, settings)
    if not ok:
        _log.info("VI 수집 스킵: %s (%s)", date_kst, reason)
        return 0

    xlsx_path = settings.vi_universe_xlsx_path
    if not args.force and date_kst in read_existing_dates(xlsx_path):
        _log.info("VI 수집 스킵: %s 이미 vi_universe.xlsx에 기록됨", date_kst)
        return 0

    try:
        settings.validate()
    except ValueError as exc:
        _log.error("설정 오류: %s", exc)
        return 1

    api = KISApiClient(settings)
    caps, names, markets = _load_market_cap_table(settings.naver_http_delay_sec)

    try:
        events = collect_vi_universe_for_date(
            api,
            date_kst,
            cap_table=caps,
            name_table=names,
            market_table=markets,
        )
    except Exception as exc:
        _log.error("VI 수집 실패: %s", exc)
        return 1

    written = append_vi_universe_xlsx_rows(xlsx_path, date_kst, events)
    _log.info(
        "VI 수집 완료 date=%s rows=%d path=%s",
        date_kst,
        written,
        xlsx_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
