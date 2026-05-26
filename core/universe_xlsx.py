"""xlsx writer for daily universe watchlist records."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import openpyxl

HEADERS = [
    "번호",
    "날짜",
    "전략",
    "종목코드",
    "종목명",
    "시가총액_억",
    "RS수익률_pct",
    "RS순위",
    "RS상위_pct",
    "52주고가",
    "vol_ma20",
    "w52_hit_60d",
    "w52_hit_10d",
    "전일종가",
    "봉개수",
    "RS통과",
    "감시포함",
    "created_at",
]

_SHEET_NAME = "universe_all"
_COL = {h: i for i, h in enumerate(HEADERS, 1)}


def ensure_universe_xlsx(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = _SHEET_NAME
    for col_idx, h in enumerate(HEADERS, 1):
        ws.cell(2, col_idx).value = h
    wb.save(path)


def _find_last_data_row(ws) -> int:
    last = 2
    for row_idx in range(3, ws.max_row + 1):
        if ws.cell(row_idx, 1).value is not None:
            last = row_idx
    return last


def read_max_no(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        max_no = 0
        for row in ws.iter_rows(min_row=3, values_only=True):
            if row[0] is None:
                continue
            try:
                max_no = max(max_no, int(row[0]))
            except Exception:
                continue
        return max_no
    except Exception:
        return 0


def append_universe_xlsx_rows(
    path: Path,
    rows: List[Dict[str, Any]],
    symbol_names: Optional[Dict[str, str]] = None,
) -> None:
    if not rows:
        return
    ensure_universe_xlsx(path)
    names = symbol_names or {}
    max_no = read_max_no(path)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    start_row = _find_last_data_row(ws) + 1

    for i, r in enumerate(rows):
        row_num = start_row + i
        no = max_no + i + 1
        sym_raw = str(r.get("symbol", "") or "").strip()
        sym = sym_raw.zfill(6) if sym_raw.isdigit() else sym_raw
        name = str(r.get("name", "") or names.get(sym, "") or "").strip()
        sym_cell: Any = int(sym) if sym.isdigit() else sym

        ws.cell(row_num, _COL["번호"]).value = no
        ws.cell(row_num, _COL["날짜"]).value = str(r.get("date_kst", "") or "")
        ws.cell(row_num, _COL["전략"]).value = int(r.get("strategy_mode", 0) or 0)
        ws.cell(row_num, _COL["종목코드"]).value = sym_cell
        ws.cell(row_num, _COL["종목명"]).value = name
        ws.cell(row_num, _COL["시가총액_억"]).value = r.get("market_cap_billion")
        rs_pct = r.get("rs_return_pct")
        ws.cell(row_num, _COL["RS수익률_pct"]).value = (
            round(float(rs_pct), 4) if rs_pct is not None else None
        )
        ws.cell(row_num, _COL["RS순위"]).value = r.get("rs_rank")
        ws.cell(row_num, _COL["RS상위_pct"]).value = r.get("rs_top_pct")
        ws.cell(row_num, _COL["52주고가"]).value = int(r.get("w52_high", 0) or 0)
        ws.cell(row_num, _COL["vol_ma20"]).value = int(r.get("vol_ma20", 0) or 0)
        ws.cell(row_num, _COL["w52_hit_60d"]).value = int(r.get("w52_hit_60d", 0) or 0)
        ws.cell(row_num, _COL["w52_hit_10d"]).value = int(r.get("w52_hit_10d", 0) or 0)
        ws.cell(row_num, _COL["전일종가"]).value = int(r.get("close", 0) or 0)
        ws.cell(row_num, _COL["봉개수"]).value = int(r.get("bar_count", 0) or 0)
        ws.cell(row_num, _COL["RS통과"]).value = str(r.get("rs_passed", "") or "")
        ws.cell(row_num, _COL["감시포함"]).value = str(r.get("watchlisted", "") or "")
        ws.cell(row_num, _COL["created_at"]).value = str(r.get("created_at_iso", "") or "")

    wb.save(path)
