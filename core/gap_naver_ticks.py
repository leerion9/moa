"""Naver Finance intraday fetch (fchart minute + sise_time ticks) for gap backfill."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import requests
from bs4 import BeautifulSoup

_log = logging.getLogger("moa")

_SISE_TIME_URL = "https://finance.naver.com/item/sise_time.naver"
_FCHART_URL = "https://fchart.stock.naver.com/siseJson.nhn"
_FCHART_ROW_RE = re.compile(
    r'\["(\d{12})"\s*,\s*([^,\]]*)\s*,\s*([^,\]]*)\s*,\s*([^,\]]*)\s*,\s*([^,\]]*)\s*,\s*([^,\]]*)'
)
_UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_MAX_PAGES = 200
# KRX regular session (same as KIS minute chart)
MARKET_OPEN_HHMMSS = "090000"
MARKET_CLOSE_HHMMSS = "153000"


@dataclass(frozen=True)
class NaverTick:
    hhmmss: str
    price: int
    volume: int


def thistime_for_ymd(ymd: str, *, hhmmss: str = "180000") -> str:
    text = str(ymd or "").strip().replace("-", "").replace(".", "")
    anchor = "".join(ch for ch in str(hhmmss or "") if ch.isdigit())
    if len(anchor) < 6:
        anchor = "180000"
    anchor = anchor[:6].ljust(6, "0")
    return f"{text}{anchor}"


def time_text_to_hhmmss(text: str) -> str:
    raw = str(text or "").strip()
    if not _TIME_RE.match(raw):
        return ""
    parts = raw.split(":")
    if len(parts) != 3:
        return ""
    return f"{int(parts[0]):02d}{parts[1]}{parts[2]}"


def parse_sise_time_html(html: str) -> List[NaverTick]:
    """Parse one Naver sise_time page. Rows are usually newest-first."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.type2")
    if not table:
        return []

    out: List[NaverTick] = []
    for tr in table.select("tr"):
        tds = tr.find_all("td")
        if len(tds) < 6:
            continue
        time_txt = tds[0].get_text(strip=True)
        if not _TIME_RE.match(time_txt):
            continue
        price_txt = tds[1].get_text(strip=True).replace(",", "")
        vol_txt = tds[5].get_text(strip=True).replace(",", "")
        if not price_txt.isdigit():
            continue
        hhmmss = time_text_to_hhmmss(time_txt)
        if not hhmmss:
            continue
        volume = int(vol_txt) if vol_txt.isdigit() else 0
        out.append(NaverTick(hhmmss=hhmmss, price=int(price_txt), volume=volume))
    return out


def fetch_sise_time_page(
    session: requests.Session,
    *,
    symbol: str,
    ymd: str,
    page: int,
    delay_sec: float = 0.0,
) -> List[NaverTick]:
    params = {
        "code": symbol,
        "thistime": thistime_for_ymd(ymd),
        "page": page,
    }
    resp = session.get(_SISE_TIME_URL, params=params, timeout=15)
    resp.encoding = "euc-kr"
    resp.raise_for_status()
    if delay_sec > 0:
        time.sleep(delay_sec)
    return parse_sise_time_html(resp.text)


def _parse_fchart_value(raw: str) -> Optional[int]:
    text = str(raw or "").strip()
    if not text or text.lower() == "null":
        return None
    text = text.replace(",", "")
    if text.lstrip("-").isdigit():
        return int(text)
    return None


def normalize_hhmmss(raw: object) -> str:
    text = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if len(text) >= 6:
        return text[:6]
    if len(text) == 4:
        return text + "00"
    if len(text) == 5:
        return text + "0"
    return ""


def in_regular_session(
    hhmmss: str,
    *,
    open_hhmmss: str = MARKET_OPEN_HHMMSS,
    close_hhmmss: str = MARKET_CLOSE_HHMMSS,
) -> bool:
    key = normalize_hhmmss(hhmmss)
    if not key:
        return False
    return open_hhmmss <= key <= close_hhmmss


def filter_regular_session_bars(
    bars: Sequence[Dict[str, object]],
    *,
    open_hhmmss: str = MARKET_OPEN_HHMMSS,
    close_hhmmss: str = MARKET_CLOSE_HHMMSS,
) -> List[Dict[str, object]]:
    """Drop pre/post-market minutes (Naver fchart may include ~08:30 and ~15:57)."""
    out: List[Dict[str, object]] = []
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        if in_regular_session(
            str(bar.get("hhmmss", "") or ""),
            open_hhmmss=open_hhmmss,
            close_hhmmss=close_hhmmss,
        ):
            out.append(dict(bar))
    return out


def datetime12_to_hhmmss(dt12: str) -> str:
    text = str(dt12 or "").strip()
    if len(text) < 12 or not text.isdigit():
        return ""
    return f"{text[8:10]}{text[10:12]}00"


def parse_fchart_minute_text(text: str) -> List[Dict[str, object]]:
    """
    Parse Naver fchart minute JSON-like payload.
    Returns oldest-first minute bars for simulate_gap_trade().
    """
    rows: List[tuple[str, int, int]] = []
    for match in _FCHART_ROW_RE.finditer(text or ""):
        dt12 = match.group(1)
        close_px = _parse_fchart_value(match.group(5))
        acml_vol = _parse_fchart_value(match.group(6))
        if close_px is None or close_px <= 0:
            continue
        hhmmss = datetime12_to_hhmmss(dt12)
        if not hhmmss:
            continue
        rows.append((hhmmss, close_px, acml_vol or 0))

    if not rows:
        return []

    rows.sort(key=lambda r: r[0])
    bars: List[Dict[str, object]] = []
    prev_acml = 0
    acml_value = 0
    for hhmmss, close_px, acml_vol in rows:
        vol_delta = max(acml_vol - prev_acml, 0) if acml_vol > 0 else 0
        if acml_vol > 0:
            prev_acml = acml_vol
        acml_value += close_px * vol_delta
        bars.append({
            "hhmmss": hhmmss,
            "price": close_px,
            "open": close_px,
            "high": close_px,
            "low": close_px,
            "volume": vol_delta,
            "acml_tr_pbmn": acml_value,
        })
    return bars


def fetch_fchart_minute_bars(
    symbol: str,
    ymd: str,
    *,
    delay_sec: float = 0.0,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, object]]:
    own = session is None
    sess = session or requests.Session()
    if own:
        sess.headers.update(_UA)
    params = {
        "symbol": symbol,
        "requestType": "1",
        "startTime": str(ymd).strip(),
        "endTime": str(ymd).strip(),
        "timeframe": "minute",
    }
    resp = sess.get(_FCHART_URL, params=params, timeout=15)
    resp.raise_for_status()
    if delay_sec > 0:
        time.sleep(delay_sec)
    return parse_fchart_minute_text(resp.text)


def fetch_intraday_minute_bars(
    symbol: str,
    ymd: str,
    *,
    delay_sec: float = 0.15,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, object]]:
    """
    Fetch intraday minute bars for a past session.
    Tries fchart minute first (recent retention), then sise_time ticks.
    """
    own = session is None
    sess = session or requests.Session()
    if own:
        sess.headers.update(_UA)

    try:
        bars = fetch_fchart_minute_bars(
            symbol,
            ymd,
            delay_sec=delay_sec,
            session=sess,
        )
        if bars:
            return bars
    except Exception as exc:
        _log.warning("fchart minute fail %s %s: %s", symbol, ymd, exc)

    ticks = fetch_all_ticks_for_day(symbol, ymd, delay_sec=delay_sec, session=sess)
    return ticks_to_minute_bars(ticks)


def fetch_all_ticks_for_day(
    symbol: str,
    ymd: str,
    *,
    delay_sec: float = 0.15,
    session: Optional[requests.Session] = None,
) -> List[NaverTick]:
    """
    Paginate Naver sise_time until empty page.
    Returns ticks oldest-first (chronological).
    """
    own = session is None
    sess = session or requests.Session()
    if own:
        sess.headers.update(_UA)

    merged: Dict[str, NaverTick] = {}
    for page in range(1, _MAX_PAGES + 1):
        try:
            batch = fetch_sise_time_page(
                sess,
                symbol=symbol,
                ymd=ymd,
                page=page,
                delay_sec=delay_sec,
            )
        except Exception as exc:
            _log.warning("sise_time fetch fail %s %s page=%s: %s", symbol, ymd, page, exc)
            break
        if not batch:
            break
        for tick in batch:
            merged[tick.hhmmss] = tick
        if len(batch) < 5:
            break

    ticks = [merged[k] for k in sorted(merged.keys())]
    return ticks


def ticks_to_minute_bars(ticks: Sequence[NaverTick]) -> List[Dict[str, object]]:
    """Convert ticks to 1-minute OHLCV bars (oldest-first) for gap simulation."""
    if not ticks:
        return []

    buckets: Dict[str, List[NaverTick]] = {}
    for tick in ticks:
        if tick.price <= 0:
            continue
        minute_key = tick.hhmmss[:4] + "00"
        buckets.setdefault(minute_key, []).append(tick)

    bars: List[Dict[str, object]] = []
    acml_value = 0
    for minute_key in sorted(buckets.keys()):
        group = sorted(buckets[minute_key], key=lambda t: t.hhmmss)
        prices = [t.price for t in group]
        vol_sum = sum(t.volume for t in group)
        for t in group:
            acml_value += t.price * max(t.volume, 0)
        bars.append({
            "hhmmss": minute_key,
            "price": prices[-1],
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "volume": vol_sum,
            "acml_tr_pbmn": acml_value,
        })
    return bars


def tick_cache_path(base_dir: Path, ymd: str, symbol: str) -> Path:
    return base_dir / ymd / f"{symbol}.json"


def minute_cache_path(base_dir: Path, ymd: str, symbol: str) -> Path:
    return base_dir / ymd / f"{symbol}_minute.json"


def save_ticks_cache(path: Path, *, symbol: str, ymd: str, ticks: Sequence[NaverTick]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol,
        "ymd": ymd,
        "ticks": [
            {"hhmmss": t.hhmmss, "price": t.price, "volume": t.volume}
            for t in ticks
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def save_minute_cache(
    path: Path,
    *,
    symbol: str,
    ymd: str,
    bars: Sequence[Dict[str, object]],
    source: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "symbol": symbol,
        "ymd": ymd,
        "source": source,
        "bars": list(bars),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_minute_cache(path: Path) -> List[Dict[str, object]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw = data.get("bars", [])
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, object]] = []
    for row in raw:
        if isinstance(row, dict) and row.get("hhmmss"):
            out.append(dict(row))
    return out


def load_ticks_cache(path: Path) -> List[NaverTick]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw = data.get("ticks", [])
    if not isinstance(raw, list):
        return []
    out: List[NaverTick] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        hhmmss = str(row.get("hhmmss", "") or "").strip()
        price = int(row.get("price", 0) or 0)
        volume = int(row.get("volume", 0) or 0)
        if hhmmss and price > 0:
            out.append(NaverTick(hhmmss=hhmmss, price=price, volume=volume))
    return out
