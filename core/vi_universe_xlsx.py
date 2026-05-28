"""xlsx writer for daily static upward VI records."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import openpyxl

from core.vi_collector_logic import ViEventRow, format_hhmmss_display
from core.xlsx_price_track import write_pct_cell

HEADERS = [
    "번호",
    "날짜",
    "vi_발동시간",
    "vi_해제시각",
    "2차vi_여부",
    "종목코드",
    "종목명",
    "시장구분",
    "vi_발동가",
    "vi_해제가",
    "해제후_최고상승_pct",
    "해제후_최저하락_pct",
    "시가총액_억",
    "vi전_거래대금",
    "시총그룹",
]

_SHEET_NAME = "vi_universe_all"
_COL = {h: i for i, h in enumerate(HEADERS, 1)}


def ensure_vi_universe_xlsx(path: Path) -> None:
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


def read_existing_dates(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        out: Set[str] = set()
        col = _COL["날짜"]
        for row_idx in range(3, ws.max_row + 1):
            val = ws.cell(row_idx, col).value
            if val is None:
                continue
            text = str(val).strip().replace("-", "").replace(".", "")
            if len(text) == 8 and text.isdigit():
                out.add(text)
        return out
    except Exception:
        return set()


def vi_event_to_row_dict(date_kst: str, event: ViEventRow) -> Dict[str, Any]:
    return {
        "date_kst": date_kst,
        "trigger_time": format_hhmmss_display(event.trigger_hhmmss),
        "release_time": format_hhmmss_display(event.release_hhmmss),
        "has_second_vi": "Y" if event.has_second_vi else "N",
        "symbol": event.symbol,
        "name": event.name,
        "market": event.market,
        "trigger_price": event.trigger_price,
        "release_price": event.release_price,
        "post_release_high_pct": event.post_release_high_pct,
        "post_release_low_pct": event.post_release_low_pct,
        "market_cap_billion": event.market_cap_billion,
        "pre_vi_trading_value": event.pre_vi_trading_value,
        "cap_group": event.cap_group,
    }


def append_vi_universe_xlsx_rows(
    path: Path,
    date_kst: str,
    events: List[ViEventRow],
) -> int:
    if not events:
        return 0
    ensure_vi_universe_xlsx(path)
    max_no = read_max_no(path)
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    start_row = _find_last_data_row(ws) + 1

    for i, event in enumerate(events):
        row_num = start_row + i
        no = max_no + i + 1
        r = vi_event_to_row_dict(date_kst, event)
        sym = str(r["symbol"])
        sym_cell: Any = int(sym) if sym.isdigit() else sym

        ws.cell(row_num, _COL["번호"]).value = no
        ws.cell(row_num, _COL["날짜"]).value = date_kst
        ws.cell(row_num, _COL["vi_발동시간"]).value = r["trigger_time"]
        ws.cell(row_num, _COL["vi_해제시각"]).value = r["release_time"]
        ws.cell(row_num, _COL["2차vi_여부"]).value = r["has_second_vi"]
        ws.cell(row_num, _COL["종목코드"]).value = sym_cell
        ws.cell(row_num, _COL["종목명"]).value = r["name"]
        ws.cell(row_num, _COL["시장구분"]).value = r["market"]
        ws.cell(row_num, _COL["vi_발동가"]).value = int(r["trigger_price"] or 0)
        ws.cell(row_num, _COL["vi_해제가"]).value = int(r["release_price"] or 0)
        write_pct_cell(ws.cell(row_num, _COL["해제후_최고상승_pct"]), r["post_release_high_pct"])
        write_pct_cell(ws.cell(row_num, _COL["해제후_최저하락_pct"]), r["post_release_low_pct"])
        cap = r.get("market_cap_billion")
        ws.cell(row_num, _COL["시가총액_억"]).value = int(cap) if cap else None
        tv = r.get("pre_vi_trading_value")
        ws.cell(row_num, _COL["vi전_거래대금"]).value = int(tv) if tv else None
        ws.cell(row_num, _COL["시총그룹"]).value = r.get("cap_group") or ""

    wb.save(path)
    return len(events)
