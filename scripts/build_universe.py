"""
장 시작 전 유니버스 캐시 빌드 배치 스크립트.

사용 예::
    python -m scripts.build_universe
    python -m scripts.build_universe --date 20260520
    python -m scripts.build_universe --bootstrap-history
    python -m scripts.build_universe --strategy 1   # 단일 전략만 (레거시)

옵션:
    --date               YYYYMMDD (기본: 오늘 KST)
    --strategy           1 또는 2 (지정 시 해당 전략만 빌드)
    --bootstrap-history  1차 필터 종목 history_cache 풀 수집
    --force              캐시가 이미 있어도 강제 재빌드
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.history_cache import HistoryCacheStore
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.universe_builder import UniverseBuilder
from core.universe_cache import cache_path, load_cache, save_cache
from core.universe_xlsx import append_universe_xlsx_rows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("moa")


def _build_history_store(api: KISApiClient) -> HistoryCacheStore:
    return HistoryCacheStore(
        settings.history_cache_dir,
        delay_sec=settings.naver_http_delay_sec,
        jitter_sec=settings.naver_request_jitter_sec,
        batch_size=settings.naver_batch_size,
        batch_pause_sec=settings.naver_batch_pause_sec,
        api=api,
    )


def main() -> None:
    settings.validate()

    p = argparse.ArgumentParser(description="유니버스 캐시 빌드")
    p.add_argument("--date", dest="ymd", help="YYYYMMDD (기본: 오늘 KST)")
    p.add_argument(
        "--strategy",
        dest="strategy",
        type=int,
        choices=[1, 2],
        help="단일 전략만 빌드 (미지정 시 전략1+2 동시)",
    )
    p.add_argument(
        "--bootstrap-history",
        action="store_true",
        help="history_cache 풀 수집 (최초 1회, 약 1시간)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="캐시가 있어도 강제 재빌드",
    )
    args = p.parse_args()

    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    ymd = args.ymd or kst_now.strftime("%Y%m%d")

    data_dir = Path("data")
    api = KISApiClient(settings=settings)

    _log.info("종목 마스터 로드 중 ...")
    symbol_names = load_or_refresh_symbol_master(
        settings.symbol_master_path,
        auto_refresh=settings.symbol_master_auto_refresh,
        max_age_days=settings.symbol_master_max_age_days,
        delay_sec=settings.naver_http_delay_sec,
    )
    _log.info("종목 마스터: %d종목", len(symbol_names))

    history_store = _build_history_store(api)
    builder = UniverseBuilder(
        api=api,
        settings=settings,
        symbol_names=symbol_names,
        history_store=history_store,
    )

    if args.bootstrap_history:
        cap_list = builder._fetch_cap_list()
        filtered = builder._apply_first_filter(cap_list)
        _log.info("history bootstrap 대상: %d종목", len(filtered))
        t0 = datetime.now()
        stats = history_store.bootstrap_all(filtered)
        elapsed = (datetime.now() - t0).total_seconds()
        print(
            f"\n[bootstrap-history] 완료 total={stats.total} "
            f"new={stats.bootstrapped} skipped={stats.skipped} "
            f"failed={stats.failed} elapsed={elapsed:.1f}s"
        )
        if args.strategy is None and not args.force:
            # bootstrap only unless user also wants universe build
            return

    if args.ymd:
        build_dt = datetime.strptime(ymd, "%Y%m%d").replace(
            hour=8, minute=30, tzinfo=ZoneInfo("Asia/Seoul")
        )
    else:
        build_dt = kst_now

    t0 = datetime.now()

    if args.strategy is not None:
        effective_settings = dataclasses.replace(settings, strategy_mode=args.strategy)
        single_builder = UniverseBuilder(
            api=api,
            settings=effective_settings,
            symbol_names=symbol_names,
            history_store=history_store,
        )
        ucache_path = cache_path(data_dir, ymd, args.strategy)
        if not args.force:
            existing = load_cache(ucache_path, strategy_mode=args.strategy)
            if existing is not None and existing.date_kst == ymd:
                print(
                    f"[build_universe] 캐시 이미 존재: {ucache_path.name} "
                    f"({len(existing.symbols)}종목)"
                )
                return
        universe = single_builder.build(now_kst=build_dt)
        save_cache(ucache_path, universe)
        elapsed = (datetime.now() - t0).total_seconds()
        print(
            f"\n[build_universe] 완료 strategy={universe.strategy_mode} "
            f"종목={len(universe.symbols)} elapsed={elapsed:.1f}s "
            f"path={ucache_path}"
        )
        return

    path_s1 = cache_path(data_dir, ymd, 1)
    path_s2 = cache_path(data_dir, ymd, 2)
    if not args.force:
        c1 = load_cache(path_s1, strategy_mode=1)
        c2 = load_cache(path_s2, strategy_mode=2)
        if (
            c1 is not None
            and c2 is not None
            and c1.date_kst == ymd
            and c2.date_kst == ymd
        ):
            print(
                f"[build_universe] dual 캐시 이미 존재: "
                f"s1={len(c1.symbols)} s2={len(c2.symbols)}"
            )
            print("  강제 재빌드: --force")
            return

    _log.info("dual 유니버스 빌드 시작 (date=%s) ...", ymd)
    try:
        result = builder.build_dual(now_kst=build_dt, bootstrap=False)
    except Exception as exc:
        _log.error("유니버스 빌드 실패: %s", exc, exc_info=True)
        sys.exit(1)

    save_cache(path_s1, result.cache_s1)
    save_cache(path_s2, result.cache_s2)
    append_universe_xlsx_rows(
        settings.universe_xlsx_path,
        result.xlsx_rows,
        symbol_names=symbol_names,
    )

    elapsed = (datetime.now() - t0).total_seconds()
    print(
        f"\n[build_universe] dual 완료!"
        f"\n  date={result.date_kst}"
        f"\n  strategy1={len(result.cache_s1.symbols)}"
        f"\n  strategy2={len(result.cache_s2.symbols)}"
        f"\n  elapsed={elapsed:.1f}s"
        f"\n  cache_s1={path_s1}"
        f"\n  cache_s2={path_s2}"
        f"\n  universe.xlsx={settings.universe_xlsx_path}"
    )


if __name__ == "__main__":
    main()
