from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    # --- KIS API 인증 ---
    app_key: str = os.getenv("APP_KEY", "")
    app_secret: str = os.getenv("APP_SECRET", "")
    account_no: str = os.getenv("ACCOUNT_NO", "")
    account_prdt_cd: str = os.getenv("ACCOUNT_PRDT_CD", "01")

    is_paper_trading: bool = os.getenv("IS_PAPER_TRADING", "true").lower() == "true"
    # 완전 시뮬레이션 모드: KIS 주문 API 미호출, 가상 1주 체결로 기록
    sim_mode: bool = os.getenv("SIM_MODE", "true").lower() == "true"
    base_url_paper: str = os.getenv(
        "BASE_URL_PAPER", "https://openapivts.koreainvestment.com:29443"
    )
    base_url_live: str = os.getenv(
        "BASE_URL_LIVE", "https://openapi.koreainvestment.com:9443"
    )

    # --- 전략 공통 ---
    strategy_mode: int = int(os.getenv("STRATEGY_MODE", "1") or "1")   # 1=최초돌파, 2=추세지속
    max_positions: int = 4                                              # 최대 동시 보유 종목 수
    allocation_per_symbol: float = 0.25                                 # 종목당 가용 현금 비율 (25%)

    # --- 1차 필터 ---
    min_market_cap_billion: int = 800       # 시총 기준 (억원), 이 미만 제외
    include_etf: bool = False               # ETF 포함 여부
    include_preferred: bool = False         # 우선주 포함 여부
    vol_ma_days: int = 20                   # 거래량 이동평균 일수
    rs_lookback_months: int = 6             # 상대강도 계산 기간 (개월)
    rs_top_pct: float = 0.10                # 상대강도 상위 N% 기준 (10% = 상위 10%)

    # --- 2차 필터 ---
    # 전략1: 최초 돌파 (최근 N일 이내 52주 신고가 없을 것)
    w52_fresh_days: int = int(os.getenv("W52_FRESH_DAYS", "60") or "60")
    # 전략2: 추세 지속 (최근 N일 내 52주 신고가 M회 이상)
    w52_cont_lookback_days: int = int(os.getenv("W52_CONT_LOOKBACK_DAYS", "10") or "10")
    w52_cont_min_hits: int = int(os.getenv("W52_CONT_MIN_HITS", "5") or "5")
    # 유니버스: 전일 종가가 52주 신고가 대비 이 비율(0.30=30%) 이상 낮으면 제외
    w52_max_gap_pct: float = float(os.getenv("W52_MAX_GAP_PCT", "0.30") or "0.30")

    # --- 매도 조건 ---
    trailing_stop_pct: float = float(os.getenv("TRAILING_STOP_PCT", "0.075") or "0.075")   # 고점 대비 -7.5% 손절

    # --- 갭상승 회복 전략 (gap_collector) ---
    gap_min_pct: float = float(os.getenv("GAP_MIN_PCT", "3.0") or "3.0")
    gap_max_pct: float = float(os.getenv("GAP_MAX_PCT", "9.0") or "9.0")
    gap_dip_min_pct: float = float(os.getenv("GAP_DIP_MIN_PCT", "3.0") or "3.0")
    gap_trailing_stop_pct: float = float(os.getenv("GAP_TRAILING_STOP_PCT", "0.05") or "0.05")
    gap_buy_qty: int = int(os.getenv("GAP_BUY_QTY", "1") or "1")
    gap_backfill_batch_size: int = int(os.getenv("GAP_BACKFILL_BATCH_SIZE", "30") or "30")
    gap_naver_tick_delay_sec: float = float(
        os.getenv("GAP_NAVER_TICK_DELAY_SEC", "0.15") or "0.15"
    )

    # --- 스케줄 ---
    monitor_start_hhmm: str = "09:00"
    monitor_end_hhmm: str = "15:30"
    result_write_hhmm: str = os.getenv("RESULT_WRITE_HHMM", "15:32")
    shutdown_hhmm: str = os.getenv("SHUTDOWN_HHMM", "15:40")

    # --- KIS API 호출 제한 ---
    order_retry_count: int = 3
    kis_min_request_interval_sec: float = float(
        os.getenv("KIS_MIN_REQUEST_INTERVAL_SEC", "0.15") or "0.15"
    )
    kis_rate_limit_retry_sleep_sec: float = float(
        os.getenv("KIS_RATE_LIMIT_RETRY_SLEEP_SEC", "1.0") or "1.0"
    )
    kis_api_retry_max: int = int(os.getenv("KIS_API_RETRY_MAX", "8") or "8")
    request_timeout_sec: int = 8
    poll_interval_sec: int = 2
    heartbeat_sec: int = int(os.getenv("HEARTBEAT_SEC", "600") or "600")

    # --- 수수료/세금 ---
    fee_rate_buy: float = 0.00015
    fee_rate_sell: float = 0.00015
    tax_rate_sell: float = 0.0018

    # --- 로그/파일 경로 ---
    log_root_dir: Path = ROOT_DIR / "data" / "logs"
    symbol_master_path: Path = ROOT_DIR / "data" / "kr_symbol_master.json"
    symbol_master_auto_refresh: bool = (
        os.getenv("SYMBOL_MASTER_AUTO_REFRESH", "true").lower() == "true"
    )
    symbol_master_max_age_days: int = int(os.getenv("SYMBOL_MASTER_MAX_AGE_DAYS", "7") or "7")
    result_csv_on_shutdown: bool = os.getenv("RESULT_CSV_ON_SHUTDOWN", "true").lower() == "true"
    result_csv_kis_lookback_days: int = int(os.getenv("RESULT_CSV_KIS_LOOKBACK_DAYS", "30") or "30")

    naver_http_delay_sec: float = float(os.getenv("NAVER_HTTP_DELAY_SEC", "0.05") or "0.05")
    naver_batch_size: int = int(os.getenv("NAVER_BATCH_SIZE", "50") or "50")
    naver_batch_pause_sec: float = float(os.getenv("NAVER_BATCH_PAUSE_SEC", "3.0") or "3.0")
    naver_request_jitter_sec: float = float(os.getenv("NAVER_REQUEST_JITTER_SEC", "0.03") or "0.03")
    history_cache_dir: Path = ROOT_DIR / "data" / "history_cache"
    gap_backfill_dir: Path = ROOT_DIR / "data" / "gap_backfill"
    gap_backfill_ticks_dir: Path = ROOT_DIR / "data" / "gap_backfill" / "ticks"

    holiday_dates_path: Path = Path(
        os.getenv("HOLIDAY_DATES_PATH", str(ROOT_DIR / "config" / "korea_market_holidays.txt"))
    )

    @property
    def base_url(self) -> str:
        return self.base_url_paper if self.is_paper_trading else self.base_url_live

    @property
    def mode_name(self) -> str:
        return "paper" if self.is_paper_trading else "live"

    @property
    def log_dir(self) -> Path:
        return self.log_root_dir / self.mode_name

    @property
    def result_csv_path(self) -> Path:
        return self.log_dir / "result.csv"

    @property
    def result_xlsx_path(self) -> Path:
        return self.log_dir / "result.xlsx"

    @property
    def universe_xlsx_path(self) -> Path:
        return self.log_dir / "universe.xlsx"

    @property
    def vi_universe_xlsx_path(self) -> Path:
        return self.log_dir / "vi_universe.xlsx"

    @property
    def gap_result_xlsx_path(self) -> Path:
        return self.log_dir / "gap_result.xlsx"

    @property
    def gap_backfill_xlsx_path(self) -> Path:
        return self.log_dir / "gap_backfill.xlsx"

    @property
    def kis_token_cache_path(self) -> Path:
        return ROOT_DIR / "data" / "kis_token_cache.json"

    @property
    def cano(self) -> str:
        if "-" in self.account_no:
            return self.account_no.split("-", maxsplit=1)[0].strip()
        return self.account_no.strip()

    @property
    def acnt_prdt_cd(self) -> str:
        if "-" in self.account_no:
            tail = self.account_no.split("-", maxsplit=1)[1].strip()
            if tail:
                return tail
        return self.account_prdt_cd.strip()

    def validate(self) -> None:
        required = {
            "APP_KEY": self.app_key,
            "APP_SECRET": self.app_secret,
            "ACCOUNT_NO": self.account_no,
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            joined = ", ".join(missing)
            raise ValueError(f"Missing .env values: {joined}")
        if len(self.cano) != 8 or not self.cano.isdigit():
            raise ValueError("ACCOUNT_NO 앞 8자리는 숫자 8자리여야 합니다. 예: 50177775-01")
        if len(self.acnt_prdt_cd) != 2 or not self.acnt_prdt_cd.isdigit():
            raise ValueError("계좌 상품코드는 숫자 2자리여야 합니다. 예: 01")
        if self.heartbeat_sec < 5:
            raise ValueError("HEARTBEAT_SEC는 5 이상이어야 합니다.")
        if self.strategy_mode not in (1, 2):
            raise ValueError("STRATEGY_MODE는 1 또는 2여야 합니다.")
        if not (0.0 < self.trailing_stop_pct < 1.0):
            raise ValueError("TRAILING_STOP_PCT는 0~1 사이여야 합니다. 예: 0.075")
        if not (0.0 <= self.w52_max_gap_pct < 1.0):
            raise ValueError("W52_MAX_GAP_PCT는 0~1 사이(1 미만)여야 합니다. 예: 0.30")
        if self.gap_min_pct < 0 or self.gap_max_pct <= self.gap_min_pct:
            raise ValueError("GAP_MIN_PCT < GAP_MAX_PCT 여야 합니다.")
        if self.gap_dip_min_pct <= 0 or self.gap_dip_min_pct >= 100:
            raise ValueError("GAP_DIP_MIN_PCT는 0~100 사이여야 합니다.")
        if not (0.0 < self.gap_trailing_stop_pct < 1.0):
            raise ValueError("GAP_TRAILING_STOP_PCT는 0~1 사이여야 합니다. 예: 0.05")
        if self.gap_buy_qty < 1:
            raise ValueError("GAP_BUY_QTY는 1 이상이어야 합니다.")
        if self.gap_backfill_batch_size < 1:
            raise ValueError("GAP_BACKFILL_BATCH_SIZE는 1 이상이어야 합니다.")
        if self.gap_naver_tick_delay_sec < 0:
            raise ValueError("GAP_NAVER_TICK_DELAY_SEC는 0 이상이어야 합니다.")
        if self.result_csv_kis_lookback_days < 1 or self.result_csv_kis_lookback_days > 90:
            raise ValueError("RESULT_CSV_KIS_LOOKBACK_DAYS는 1~90이어야 합니다.")


settings = Settings()
