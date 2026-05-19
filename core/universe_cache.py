from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class CachedSymbol:
    w52_high: int           # 52주 최고가 (원)
    vol_ma20: int           # 20일 평균 거래량
    w52_hit_60d: int        # 최근 60일 내 52주 신고가 터치 횟수 (전략1 필터용)
    w52_hit_10d: int        # 최근 10일 내 52주 신고가 터치 횟수 (전략2 필터용)


@dataclass(frozen=True)
class UniverseCache:
    date_kst: str           # YYYYMMDD
    strategy_mode: int      # 1 or 2
    created_at_iso: str
    symbols: Dict[str, CachedSymbol]


def today_kst_yyyymmdd(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(ZoneInfo("Asia/Seoul"))
    return dt.strftime("%Y%m%d")


def cache_path(base_dir: Path, date_kst: str) -> Path:
    return base_dir / f"universe_cache_{date_kst}.json"


def load_cache(path: Path, strategy_mode: int) -> Optional[UniverseCache]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if str(data.get("strategy_mode", "")) != str(strategy_mode):
        return None

    symbols_raw = data.get("symbols", {}) or {}
    symbols: Dict[str, CachedSymbol] = {}
    for sym, row in symbols_raw.items():
        try:
            symbols[str(sym)] = CachedSymbol(
                w52_high=int(row["w52_high"]),
                vol_ma20=int(row["vol_ma20"]),
                w52_hit_60d=int(row.get("w52_hit_60d", 0)),
                w52_hit_10d=int(row.get("w52_hit_10d", 0)),
            )
        except Exception:
            continue

    if not symbols:
        return None

    return UniverseCache(
        date_kst=str(data.get("date_kst", "")),
        strategy_mode=int(data.get("strategy_mode", 0)),
        created_at_iso=str(data.get("created_at_iso", "")),
        symbols=symbols,
    )


def save_cache(path: Path, cache: UniverseCache) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "date_kst": cache.date_kst,
        "strategy_mode": cache.strategy_mode,
        "created_at_iso": cache.created_at_iso,
        "symbols": {
            sym: {
                "w52_high": row.w52_high,
                "vol_ma20": row.vol_ma20,
                "w52_hit_60d": row.w52_hit_60d,
                "w52_hit_10d": row.w52_hit_10d,
            }
            for sym, row in cache.symbols.items()
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
