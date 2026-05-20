"""
장 시작 전 유니버스 캐시 빌드 배치 스크립트.

사용 예::
    python -m scripts.build_universe
    python -m scripts.build_universe --date 20260520
    python -m scripts.build_universe --strategy 2

옵션:
    --date      YYYYMMDD (기본: 오늘 KST)
    --strategy  1 또는 2 (기본: settings.strategy_mode)
    --force     캐시가 이미 있어도 강제 재빌드
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.universe_builder import UniverseBuilder
from core.universe_cache import cache_path, load_cache, save_cache

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("moa")


def main() -> None:
    settings.validate()

    p = argparse.ArgumentParser(description="유니버스 캐시 빌드")
    p.add_argument("--date", dest="ymd", help="YYYYMMDD (기본: 오늘 KST)")
    p.add_argument(
        "--strategy",
        dest="strategy",
        type=int,
        choices=[1, 2],
        help="전략 모드 (기본: settings.strategy_mode)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="캐시가 있어도 강제 재빌드",
    )
    args = p.parse_args()

    kst_now = datetime.now(ZoneInfo("Asia/Seoul"))
    ymd = args.ymd or kst_now.strftime("%Y%m%d")

    # settings는 frozen dataclass이므로 strategy_mode를 override해야 하는 경우
    # 별도 object로 관리하지 않고, builder에 넘기기 전에 effective_mode를 결정한다.
    effective_mode = args.strategy or settings.strategy_mode

    # settings의 strategy_mode가 다른 경우를 위한 래퍼
    import dataclasses
    effective_settings = dataclasses.replace(settings, strategy_mode=effective_mode)

    data_dir = Path("data")
    ucache_path = cache_path(data_dir, ymd)

    # 캐시 존재 확인
    if not args.force:
        existing = load_cache(ucache_path, strategy_mode=effective_mode)
        if existing is not None and existing.date_kst == ymd:
            print(
                f"[build_universe] 캐시 이미 존재: {ucache_path.name} "
                f"({len(existing.symbols)}종목, strategy={existing.strategy_mode})"
            )
            print("  강제 재빌드 하려면 --force 옵션을 사용하세요.")
            return

    # 종목 마스터 로드 (ETF 필터용)
    _log.info("종목 마스터 로드 중 ...")
    symbol_names = load_or_refresh_symbol_master(
        settings.symbol_master_path,
        auto_refresh=settings.symbol_master_auto_refresh,
        max_age_days=settings.symbol_master_max_age_days,
        delay_sec=settings.naver_http_delay_sec,
    )
    _log.info("종목 마스터: %d종목", len(symbol_names))

    # 유니버스 빌드
    api = KISApiClient(settings=effective_settings)
    builder = UniverseBuilder(
        api=api,
        settings=effective_settings,
        symbol_names=symbol_names,
    )

    _log.info("유니버스 빌드 시작 (date=%s strategy=%s) ...", ymd, effective_mode)
    t0 = datetime.now()

    # date 파라미터가 지정된 경우 now_kst를 해당 날짜 08:30으로 설정
    if args.ymd:
        build_dt = datetime.strptime(ymd, "%Y%m%d").replace(
            hour=8, minute=30, tzinfo=ZoneInfo("Asia/Seoul")
        )
    else:
        build_dt = kst_now

    try:
        universe = builder.build(now_kst=build_dt)
    except Exception as exc:
        _log.error("유니버스 빌드 실패: %s", exc, exc_info=True)
        sys.exit(1)

    elapsed = (datetime.now() - t0).total_seconds()

    # 저장
    save_cache(ucache_path, universe)

    print(
        f"\n[build_universe] 완료!"
        f"\n  date={universe.date_kst}"
        f"\n  strategy={universe.strategy_mode}"
        f"\n  종목 수={len(universe.symbols)}"
        f"\n  소요시간={elapsed:.1f}초"
        f"\n  저장위치={ucache_path}"
    )

    if universe.symbols:
        sample = list(universe.symbols.items())[:5]
        print("\n  [샘플 상위 5종목]")
        for sym, feat in sample:
            name = symbol_names.get(sym, "")
            print(
                f"  {sym} {name:12s} | 52주고가={feat.w52_high:,} "
                f"vol_ma20={feat.vol_ma20:,} "
                f"hit60d={feat.w52_hit_60d} hit10d={feat.w52_hit_10d}"
            )


if __name__ == "__main__":
    main()
