"""Persistent Naver daily-bar cache with incremental updates."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

import requests

from core.api_client import SymbolHistory

_log = logging.getLogger("moa")

KST = ZoneInfo("Asia/Seoul")
W52_ROLLING_DAYS = 252
MAX_STORED_BARS = 320
MIN_FULL_BARS = 126
NAVER_FULL_PAGES = 30
NAVER_INCREMENTAL_PAGES = 1
_FETCH_RETRIES = 4
_RETRY_BACKOFF_SEC = (5.0, 15.0, 30.0, 60.0)


@dataclass(frozen=True)
class HistoryCacheStats:
    total: int = 0
    bootstrapped: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


def merge_bars(existing: List[Dict], incoming: List[Dict]) -> List[Dict]:
    """Merge OHLCV bars by date, newest-first order."""
    by_date: Dict[str, Dict] = {}
    for bar in existing + incoming:
        date_key = str(bar.get("date", "") or "").strip()
        if not date_key:
            continue
        by_date[date_key] = {
            "date": date_key,
            "open": int(bar.get("open", 0) or 0),
            "high": int(bar.get("high", 0) or 0),
            "low": int(bar.get("low", 0) or 0),
            "close": int(bar.get("close", 0) or 0),
            "volume": int(bar.get("volume", 0) or 0),
        }
    merged = sorted(by_date.values(), key=lambda b: b["date"], reverse=True)
    return merged[:MAX_STORED_BARS]


def compute_w52_from_bars(bars: List[Dict], rolling_days: int = W52_ROLLING_DAYS) -> tuple[int, int]:
    window = bars[:rolling_days]
    if not window:
        return 0, 0
    highs = [int(b.get("high", 0) or 0) for b in window]
    lows = [int(b.get("low", 0) or 0) for b in window if int(b.get("low", 0) or 0) > 0]
    if not highs:
        return 0, 0
    return max(highs), (min(lows) if lows else 0)


def bars_to_symbol_history(symbol: str, bars: List[Dict]) -> SymbolHistory:
    w52_high, w52_low = compute_w52_from_bars(bars)
    return SymbolHistory(symbol=symbol, w52_high=w52_high, w52_low=w52_low, bars=bars)


def symbol_cache_path(base_dir: Path, symbol: str) -> Path:
    return base_dir / f"{symbol}.json"


def load_symbol_bars(path: Path) -> Optional[Dict]:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    bars = data.get("bars")
    if not isinstance(bars, list) or not bars:
        return None
    return data


def save_symbol_bars(
    path: Path,
    symbol: str,
    bars: List[Dict],
    *,
    full_pages: int,
    updated_at_iso: Optional[str] = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol,
        "updated_at_iso": updated_at_iso or datetime.now(KST).isoformat(),
        "full_pages": int(full_pages),
        "bars": merge_bars([], bars),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def is_full_cache(data: Optional[Dict]) -> bool:
    if not data:
        return False
    bars = data.get("bars") or []
    if len(bars) >= MIN_FULL_BARS:
        return True
    try:
        return int(data.get("full_pages", 0) or 0) >= NAVER_FULL_PAGES
    except Exception:
        return False


class HistoryCacheStore:
    """Load/save per-symbol Naver history with throttled HTTP."""

    def __init__(
        self,
        base_dir: Path,
        *,
        delay_sec: float = 0.05,
        jitter_sec: float = 0.03,
        batch_size: int = 50,
        batch_pause_sec: float = 3.0,
        api=None,
    ) -> None:
        self.base_dir = base_dir
        self.delay_sec = max(0.0, float(delay_sec))
        self.jitter_sec = max(0.0, float(jitter_sec))
        self.batch_size = max(1, int(batch_size))
        self.batch_pause_sec = max(0.0, float(batch_pause_sec))
        self.api = api
        self._session: Optional[requests.Session] = None
        self._request_count = 0

    def _get_session(self) -> requests.Session:
        if self._session is None:
            from core.naver_universe import _UA

            self._session = requests.Session()
            self._session.headers.update(_UA)
        return self._session

    def _throttle(self) -> None:
        jitter = random.uniform(0.0, self.jitter_sec) if self.jitter_sec > 0 else 0.0
        time.sleep(self.delay_sec + jitter)

    def _maybe_batch_pause(self) -> None:
        if self._request_count <= 0:
            return
        if self._request_count % self.batch_size != 0:
            return
        extra = random.uniform(0.5, 1.5)
        pause = self.batch_pause_sec + extra
        _log.info("[HC] batch pause %.1fs after %d requests", pause, self._request_count)
        time.sleep(pause)

    def _after_request(self) -> None:
        self._request_count += 1
        self._throttle()
        self._maybe_batch_pause()

    def _fetch_naver_pages(
        self,
        symbol: str,
        pages: int,
        session: requests.Session,
    ) -> Optional[List[Dict]]:
        from core.naver_universe import fetch_symbol_history_naver

        last_exc: Optional[Exception] = None
        for attempt in range(_FETCH_RETRIES):
            try:
                hist = fetch_symbol_history_naver(
                    symbol=symbol,
                    pages=pages,
                    delay_sec=self.delay_sec,
                    session=session,
                )
                self._after_request()
                if hist is None or not hist.bars:
                    if attempt + 1 < _FETCH_RETRIES:
                        backoff = _RETRY_BACKOFF_SEC[min(attempt, len(_RETRY_BACKOFF_SEC) - 1)]
                        _log.warning(
                            "[HC] empty response %s pages=%s retry in %.0fs",
                            symbol,
                            pages,
                            backoff,
                        )
                        time.sleep(backoff)
                        continue
                    return None
                return list(hist.bars)
            except Exception as exc:
                last_exc = exc
                backoff = _RETRY_BACKOFF_SEC[min(attempt, len(_RETRY_BACKOFF_SEC) - 1)]
                _log.warning(
                    "[HC] fetch fail %s pages=%s attempt=%s: %s (sleep %.0fs)",
                    symbol,
                    pages,
                    attempt + 1,
                    exc,
                    backoff,
                )
                time.sleep(backoff)
        if last_exc:
            _log.debug("[HC] fetch exhausted %s: %s", symbol, last_exc)
        return None

    def _fetch_kis_history(self, symbol: str) -> Optional[List[Dict]]:
        if self.api is None:
            return None
        try:
            from core.universe_builder import HISTORY_DAYS

            hist = self.api.get_symbol_history(symbol, days=HISTORY_DAYS)
            if hist and hist.bars:
                return list(hist.bars)
        except Exception as exc:
            _log.debug("[HC] KIS fallback fail %s: %s", symbol, exc)
        return None

    def load_history(self, symbol: str) -> Optional[SymbolHistory]:
        path = symbol_cache_path(self.base_dir, symbol)
        data = load_symbol_bars(path)
        if not data:
            return None
        bars = merge_bars([], list(data.get("bars") or []))
        if not bars:
            return None
        return bars_to_symbol_history(symbol, bars)

    def load_histories(self, symbols: List[str]) -> Dict[str, SymbolHistory]:
        out: Dict[str, SymbolHistory] = {}
        for sym in symbols:
            hist = self.load_history(sym)
            if hist is not None:
                out[sym] = hist
        return out

    def bootstrap_symbol(self, symbol: str, session: Optional[requests.Session] = None) -> bool:
        session = session or self._get_session()
        bars = self._fetch_naver_pages(symbol, NAVER_FULL_PAGES, session)
        if not bars:
            bars = self._fetch_kis_history(symbol)
        if not bars:
            return False
        path = symbol_cache_path(self.base_dir, symbol)
        save_symbol_bars(path, symbol, bars, full_pages=NAVER_FULL_PAGES)
        return True

    def update_symbol(self, symbol: str, session: Optional[requests.Session] = None) -> str:
        """Return status: bootstrapped | updated | skipped | failed."""
        session = session or self._get_session()
        path = symbol_cache_path(self.base_dir, symbol)
        data = load_symbol_bars(path)

        if not is_full_cache(data):
            ok = self.bootstrap_symbol(symbol, session)
            return "bootstrapped" if ok else "failed"

        incoming = self._fetch_naver_pages(symbol, NAVER_INCREMENTAL_PAGES, session)
        if not incoming:
            return "skipped"

        existing = list((data or {}).get("bars") or [])
        merged = merge_bars(existing, incoming)
        full_pages = int((data or {}).get("full_pages", NAVER_FULL_PAGES) or NAVER_FULL_PAGES)
        save_symbol_bars(path, symbol, merged, full_pages=full_pages)
        return "updated"

    def bootstrap_all(self, symbols: List[str]) -> HistoryCacheStats:
        stats = HistoryCacheStats(total=len(symbols))
        session = self._get_session()
        log_every = max(1, len(symbols) // 10)
        for idx, sym in enumerate(symbols):
            if idx % log_every == 0:
                _log.info("[HC] bootstrap %d/%d ...", idx, len(symbols))
            path = symbol_cache_path(self.base_dir, sym)
            if is_full_cache(load_symbol_bars(path)):
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped,
                    updated=stats.updated,
                    skipped=stats.skipped + 1,
                    failed=stats.failed,
                )
                continue
            if self.bootstrap_symbol(sym, session):
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped + 1,
                    updated=stats.updated,
                    skipped=stats.skipped,
                    failed=stats.failed,
                )
            else:
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped,
                    updated=stats.updated,
                    skipped=stats.skipped,
                    failed=stats.failed + 1,
                )
        _log.info(
            "[HC] bootstrap done total=%d new=%d skipped=%d failed=%d",
            stats.total,
            stats.bootstrapped,
            stats.skipped,
            stats.failed,
        )
        return stats

    def update_all(self, symbols: List[str]) -> HistoryCacheStats:
        stats = HistoryCacheStats(total=len(symbols))
        session = self._get_session()
        log_every = max(1, len(symbols) // 10)
        for idx, sym in enumerate(symbols):
            if idx % log_every == 0:
                _log.info("[HC] update %d/%d ...", idx, len(symbols))
            status = self.update_symbol(sym, session)
            if status == "bootstrapped":
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped + 1,
                    updated=stats.updated,
                    skipped=stats.skipped,
                    failed=stats.failed,
                )
            elif status == "updated":
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped,
                    updated=stats.updated + 1,
                    skipped=stats.skipped,
                    failed=stats.failed,
                )
            elif status == "skipped":
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped,
                    updated=stats.updated,
                    skipped=stats.skipped + 1,
                    failed=stats.failed,
                )
            else:
                stats = HistoryCacheStats(
                    total=stats.total,
                    bootstrapped=stats.bootstrapped,
                    updated=stats.updated,
                    skipped=stats.skipped,
                    failed=stats.failed + 1,
                )
        _log.info(
            "[HC] update done total=%d bootstrap=%d updated=%d skipped=%d failed=%d",
            stats.total,
            stats.bootstrapped,
            stats.updated,
            stats.skipped,
            stats.failed,
        )
        return stats
