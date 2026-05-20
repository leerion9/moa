from pathlib import Path
from types import SimpleNamespace

from core.trading_day import load_manual_holiday_set, should_run_bot_today_kst
from scripts.update_holidays import update_holiday_file, _is_weekday


def _settings_with_holidays(tmp_path: Path, content: str) -> SimpleNamespace:
    p = tmp_path / "h.txt"
    p.write_text(content, encoding="utf-8")
    return SimpleNamespace(holiday_dates_path=p)


def test_weekend_saturday_blocked_without_file(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "")
    (tmp_path / "h.txt").unlink()
    ok, msg = should_run_bot_today_kst("20260404", s)
    assert ok is False
    assert "휴장일" in msg


def test_weekday_in_list_blocked(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "20250402\n")
    ok, msg = should_run_bot_today_kst("20250402", s)
    assert ok is False


def test_weekday_not_in_list_runs(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "20250402\n")
    ok, msg = should_run_bot_today_kst("20250403", s)
    assert ok is True
    assert msg == ""


def test_comments_and_blank_skipped(tmp_path: Path):
    s = _settings_with_holidays(
        tmp_path,
        "# comment\n\n20250402\n  20250403  \n",
    )
    h = load_manual_holiday_set(s.holiday_dates_path)
    assert h == {"20250402", "20250403"}


def test_sunday_blocked_even_if_in_file(tmp_path: Path):
    s = _settings_with_holidays(tmp_path, "")
    ok, msg = should_run_bot_today_kst("20260405", s)
    assert ok is False


# ---------------------------------------------------------------------------
# update_holiday_file / _is_weekday 테스트
# ---------------------------------------------------------------------------

def test_is_weekday_weekdays():
    assert _is_weekday("20260302") is True   # 월요일
    assert _is_weekday("20260306") is True   # 금요일


def test_is_weekday_weekend():
    assert _is_weekday("20260307") is False  # 토요일
    assert _is_weekday("20260308") is False  # 일요일


def test_is_weekday_invalid():
    assert _is_weekday("invalid") is False
    assert _is_weekday("") is False


def test_update_holiday_file_append_new(tmp_path: Path):
    """새 날짜를 추가할 때 기존 내용 보존 확인."""
    p = tmp_path / "holidays.txt"
    p.write_text("# 기존 주석\n20260101\n", encoding="utf-8")

    update_holiday_file(p, 2027, {"20270101", "20270301"}, force=False)

    content = p.read_text(encoding="utf-8")
    assert "20260101" in content   # 기존 항목 보존
    assert "20270101" in content   # 신규 항목 추가
    assert "20270301" in content


def test_update_holiday_file_no_duplicate(tmp_path: Path):
    """이미 있는 날짜는 중복 추가하지 않음."""
    p = tmp_path / "holidays.txt"
    p.write_text("20270101\n", encoding="utf-8")

    update_holiday_file(p, 2027, {"20270101"}, force=False)

    content = p.read_text(encoding="utf-8")
    assert content.count("20270101") == 1


def test_update_holiday_file_force_replaces(tmp_path: Path):
    """force=True 시 해당 연도 기존 항목을 제거하고 새로 작성."""
    p = tmp_path / "holidays.txt"
    p.write_text(
        "# 2027년 평일 공휴일 (KIS API 자동 갱신)\n20270101\n20270301\n",
        encoding="utf-8",
    )

    update_holiday_file(p, 2027, {"20270901", "20271003"}, force=True)

    content = p.read_text(encoding="utf-8")
    assert "20270101" not in content   # 기존 항목 제거
    assert "20270901" in content       # 새 항목 추가
    assert "20271003" in content


def test_update_holiday_file_creates_file(tmp_path: Path):
    """파일이 없을 때 새로 생성."""
    p = tmp_path / "subdir" / "holidays.txt"

    update_holiday_file(p, 2027, {"20270101"}, force=False)

    assert p.exists()
    assert "20270101" in p.read_text(encoding="utf-8")
