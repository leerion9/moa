"""
KIS API (CTCA0903R chk-holiday)를 이용해 연도별 평일 휴장일을 자동 갱신.

사용 예::
    python -m scripts.update_holidays              # 내년 데이터 추가
    python -m scripts.update_holidays --year 2027  # 특정 연도
    python -m scripts.update_holidays --year 2027 --force  # 해당 연도 기존 항목 삭제 후 재작성

동작::
    1. 대상 연도 각 월의 1일을 BASS_DT로 KIS API 호출 (월 1회 = 12회/년)
    2. 응답 rows에서 opnd_yn='N'(휴장)이고 월~금인 날만 수집
    3. korea_market_holidays.txt에 해당 연도 블록을 추가(또는 교체)
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Set
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("moa")

_KST = ZoneInfo("Asia/Seoul")
_PAUSE_SEC = 0.5  # KIS 요청 간격


def _is_weekday(ymd: str) -> bool:
    """YYYYMMDD 문자열이 평일(월~금)이면 True."""
    try:
        d = datetime.strptime(ymd, "%Y%m%d")
        return d.weekday() < 5
    except ValueError:
        return False


def fetch_closed_weekdays_for_year(api: KISApiClient, year: int) -> Set[str]:
    """
    KIS API를 월별로 호출해 연도 전체의 평일 휴장일 집합을 반환.

    Returns:
        {'YYYYMMDD', ...} — 평일이면서 opnd_yn='N'인 날짜 집합
    """
    closed: Set[str] = set()

    for month in range(1, 13):
        bass_dt = f"{year}{month:02d}01"
        _log.info("KIS 휴장일 조회: BASS_DT=%s", bass_dt)
        try:
            rows = api.get_holiday_info(bass_dt)
        except Exception as exc:
            _log.warning("  조회 실패 (BASS_DT=%s): %s", bass_dt, exc)
            time.sleep(_PAUSE_SEC)
            continue

        for row in rows:
            # 날짜 필드: bass_dt, BASS_DT 등 키 이름 차이 흡수
            row_date = ""
            for k in ("bass_dt", "BASS_DT", "bzdy_yn", "BZDY_YN"):
                v = str(row.get(k, "") or "").strip()
                if len(v) == 8 and v.isdigit():
                    row_date = v
                    break

            # opnd_yn: Y=개장, N=휴장
            opnd_yn = ""
            for k in row:
                if str(k).strip().upper() == "OPND_YN":
                    opnd_yn = str(row[k] or "").strip().upper()
                    break

            if not row_date:
                continue
            # 해당 연도 범위 내 날짜만 수집
            if not row_date.startswith(str(year)):
                continue
            if opnd_yn == "N" and _is_weekday(row_date):
                closed.add(row_date)

        time.sleep(_PAUSE_SEC)

    return closed


def _read_holiday_file_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _year_block_header(year: int) -> str:
    return f"# {year}년 평일 공휴일 (KIS API 자동 갱신)"


def update_holiday_file(path: Path, year: int, closed_days: Set[str], force: bool) -> None:
    """
    korea_market_holidays.txt에 연도 블록을 추가 또는 교체.

    force=True면 해당 연도 기존 항목(날짜 줄 + 주석 줄)을 모두 제거 후 재작성.
    force=False면 새 날짜만 append (중복 방지).
    """
    lines = _read_holiday_file_lines(path)

    if force:
        # 해당 연도 날짜 행과 연도 자동갱신 주석 제거
        year_prefix = str(year)
        filtered: List[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(year_prefix) and len(stripped) == 8 and stripped.isdigit():
                continue
            if stripped.startswith(f"# {year}년") and "자동 갱신" in stripped:
                continue
            filtered.append(line)
        lines = filtered

    # 이미 파일에 있는 날짜
    existing: Set[str] = set()
    for line in lines:
        s = line.strip()
        if len(s) == 8 and s.isdigit():
            existing.add(s)

    new_days = sorted(closed_days - existing)
    if not new_days and not force:
        _log.info("추가할 새 날짜 없음. 파일 변경하지 않습니다.")
        return

    # 파일 끝 빈줄 정리
    while lines and lines[-1].strip() == "":
        lines.pop()

    lines.append("")
    lines.append(_year_block_header(year))
    for ymd in sorted(closed_days if force else new_days):
        lines.append(ymd)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _log.info(
        "휴장일 파일 갱신: %s  (%d일 추가, %d일 총 보유)",
        path,
        len(new_days),
        len(existing) + len(new_days),
    )


def main() -> None:
    settings.validate()

    p = argparse.ArgumentParser(description="KIS API로 평일 휴장일 목록 자동 갱신")
    p.add_argument(
        "--year",
        type=int,
        default=datetime.now(_KST).year + 1,
        help="대상 연도 (기본: 내년)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="해당 연도 기존 항목을 삭제하고 재작성",
    )
    args = p.parse_args()

    year: int = args.year
    holiday_path: Path = settings.holiday_dates_path

    _log.info("=== 휴장일 자동 갱신 시작 (year=%d) ===", year)
    _log.info("대상 파일: %s", holiday_path)

    api = KISApiClient(settings=settings)
    closed = fetch_closed_weekdays_for_year(api, year)

    if not closed:
        _log.warning(
            "KIS API에서 %d년 휴장일을 가져오지 못했습니다. "
            "API 인증 상태 및 BASS_DT 응답을 확인하세요.",
            year,
        )
        return

    _log.info("KIS 응답 평일 휴장일 수: %d일", len(closed))
    for d in sorted(closed):
        _log.info("  %s", d)

    update_holiday_file(holiday_path, year, closed, force=args.force)
    print(f"\n[update_holidays] {year}년 평일 휴장일 {len(closed)}일 → {holiday_path}")


if __name__ == "__main__":
    main()
