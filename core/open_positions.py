"""Open stock positions for startup: exclude held symbols from buy watchlist."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from zoneinfo import ZoneInfo

from core.api_client import KISApiClient
from core.result_csv import Exec, _norm_symbol_6

KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    qty: int
    avg_buy_price: int


def open_positions_from_execs(execs: List[Exec]) -> Dict[str, OpenPosition]:
    """FIFO 체결 목록에서 미청산 매수 잔량 반환 (종목별 lot 단가 유지)."""
    lots: Dict[str, List[tuple[int, int]]] = {}  # symbol -> [(qty, unit_price), ...]
    for ex in execs:
        if ex.side == "BUY":
            px = int(round(ex.avg_px)) if ex.avg_px > 0 else int(round(ex.amount / ex.qty))
            lots.setdefault(ex.symbol, []).append((ex.qty, px))
            continue
        need = ex.qty
        sym_lots = lots.get(ex.symbol, [])
        idx = 0
        while need > 0 and idx < len(sym_lots):
            q, px = sym_lots[idx]
            take = min(need, q)
            q -= take
            need -= take
            if q <= 0:
                sym_lots.pop(idx)
            else:
                sym_lots[idx] = (q, px)
                idx += 1
        if sym_lots:
            lots[ex.symbol] = sym_lots
        elif ex.symbol in lots:
            del lots[ex.symbol]

    out: Dict[str, OpenPosition] = {}
    for sym, sym_lots in lots.items():
        qty = sum(q for q, _ in sym_lots)
        if qty <= 0:
            continue
        cost = sum(q * px for q, px in sym_lots)
        out[sym] = OpenPosition(symbol=sym, qty=qty, avg_buy_price=int(round(cost / qty)))
    return out


def _parse_trade_ts(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def trades_csv_to_execs(path: Path) -> List[Exec]:
    if not path.exists():
        return []
    execs: List[Exec] = []
    with path.open("r", newline="", encoding="utf-8") as fp:
        for row in csv.DictReader(fp):
            side = str(row.get("side", "") or "").strip().upper()
            if side not in ("BUY", "SELL"):
                continue
            sym = _norm_symbol_6(row.get("symbol", ""))
            if len(sym) != 6 or not sym.isdigit():
                continue
            try:
                qty = abs(int(float(str(row.get("qty", "0") or "0").replace(",", ""))))
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            try:
                price = abs(int(float(str(row.get("price", "0") or "0").replace(",", ""))))
            except Exception:
                price = 0
            if price <= 0:
                continue
            ts = _parse_trade_ts(str(row.get("ts", "") or ""))
            if ts is None:
                continue
            execs.append(
                Exec(
                    ts=ts,
                    side=side,
                    symbol=sym,
                    qty=qty,
                    amount=float(qty * price),
                    fee=0.0,
                    tax=0.0,
                    avg_px=float(price),
                )
            )
    execs.sort(key=lambda e: (e.ts, 0 if e.side == "BUY" else 1))
    return execs


def load_open_positions_from_trades_csv(path: Path) -> Dict[str, OpenPosition]:
    """trades.csv FIFO 기준 미청산 보유 (SIM_MODE 등)."""
    return open_positions_from_execs(trades_csv_to_execs(path))


def load_open_positions_from_kis(api: KISApiClient) -> Dict[str, OpenPosition]:
    """KIS 잔고 API 기준 보유 종목."""
    out: Dict[str, OpenPosition] = {}
    for row in api.get_domestic_balance_positions():
        sym = _norm_symbol_6(row.get("symbol", ""))
        if len(sym) != 6 or not sym.isdigit():
            continue
        qty = int(row.get("qty", 0) or 0)
        if qty <= 0:
            continue
        avg_raw = row.get("pchs_avg_prvs", 0) or 0
        try:
            avg = int(round(float(avg_raw)))
        except Exception:
            avg = 0
        out[sym] = OpenPosition(symbol=sym, qty=qty, avg_buy_price=avg)
    return out
