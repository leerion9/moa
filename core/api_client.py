from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Dict, List, Optional, Tuple

import requests
from zoneinfo import ZoneInfo

from config.settings import Settings

_log = logging.getLogger("moa")


def _safe_abs_int(val: object, default: int = 0) -> int:
    """KIS 응답 필드를 안전하게 int로 변환. 콤마·None·빈 문자열 허용."""
    try:
        return abs(int(str(val or "").replace(",", "") or default))
    except Exception:
        return default


def _kis_json_payload_rate_limited(data: object) -> bool:
    if not isinstance(data, dict):
        return False
    msg = str(data.get("msg1", "") or data.get("message", "") or "")
    return "EGW00201" in msg or "초당 거래건수" in msg


def _response_is_rate_limited(resp: requests.Response) -> bool:
    if resp.status_code == 429:
        return True
    try:
        data = resp.json()
    except Exception:
        txt = resp.text or ""
        return "EGW00201" in txt or "초당 거래건수" in txt
    return _kis_json_payload_rate_limited(data)


@dataclass
class Quote:
    symbol: str
    current_price: int
    open_price: int
    volume: int
    prev_high: int
    prev_low: int
    w52_high: int = 0   # 52주 최고가 (0=미지원/조회불가)
    w52_low: int = 0    # 52주 최저가


@dataclass
class SymbolHistory:
    """FHKST03010100 단일 호출로 조회한 52주 고저가 + 일봉 데이터."""
    symbol: str
    w52_high: int               # 52주 최고가
    w52_low: int                # 52주 최저가
    bars: List[Dict[str, object]]  # 최신순 OHLCV rows (date, open, high, low, close, volume)


class KISApiClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.token: str = ""
        self.token_expire_at: Optional[datetime] = None
        self._last_api_monotonic: float = 0.0
        self._server_time_offset_sec: float = 0.0
        self._server_time_offset_updated_at: float = 0.0

    def _token_is_valid(self) -> bool:
        return bool(self.token) and self.token_expire_at is not None and datetime.now() < self.token_expire_at

    def ensure_token(self) -> None:
        if self._token_is_valid():
            return

        url = f"{self.settings.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
        }
        resp = self.session.post(url, json=payload, timeout=self.settings.request_timeout_sec)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(
                "KIS token request failed. Check APP_KEY/APP_SECRET, API 신청 상태, 접근 IP 허용 설정."
            ) from exc
        self._update_server_time_offset_from_response(resp)
        data = resp.json()
        self.token = data["access_token"]
        self.token_expire_at = datetime.now() + timedelta(hours=23)

    def server_time_offset_sec(self) -> float:
        return float(self._server_time_offset_sec or 0.0)

    def now_kst(self) -> datetime:
        ts = time.time() + self.server_time_offset_sec()
        return datetime.fromtimestamp(ts, tz=ZoneInfo("Asia/Seoul"))

    def _update_server_time_offset_from_response(self, resp: requests.Response) -> None:
        try:
            date_hdr = (resp.headers or {}).get("Date", "")
            if not date_hdr:
                return
            server_dt = parsedate_to_datetime(date_hdr)
            if server_dt.tzinfo is None:
                return
            server_ts = server_dt.timestamp()
            local_ts = time.time()
            self._server_time_offset_sec = float(server_ts - local_ts)
            self._server_time_offset_updated_at = local_ts
        except Exception:
            return

    def _kis_retry_max(self) -> int:
        return int(getattr(self.settings, "kis_api_retry_max", 8))

    def _kis_rate_sleep(self) -> float:
        return float(getattr(self.settings, "kis_rate_limit_retry_sleep_sec", 1.0))

    def _pace_api(self) -> None:
        gap = float(getattr(self.settings, "kis_min_request_interval_sec", 0.15))
        if gap <= 0:
            return
        now = time.monotonic()
        wait = gap - (now - self._last_api_monotonic)
        if wait > 0:
            time.sleep(wait)
        self._last_api_monotonic = time.monotonic()

    def _headers(self, tr_id: str) -> Dict[str, str]:
        self.ensure_token()
        self._pace_api()
        return {
            "authorization": f"Bearer {self.token}",
            "appkey": self.settings.app_key,
            "appsecret": self.settings.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
            "content-type": "application/json",
        }

    def _request_get_json(
        self,
        url: str,
        *,
        tr_id: str,
        params: Dict[str, str],
        error_prefix: str,
    ) -> dict:
        last_error: Optional[Exception] = None
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(tr_id=tr_id),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400:
                    if _response_is_rate_limited(resp):
                        time.sleep(self._kis_rate_sleep())
                        last_error = RuntimeError(_http_error_detail(resp))
                        continue
                    detail = _http_error_detail(resp)
                    raise RuntimeError(f"{error_prefix}: {detail}")
                data = resp.json()
                if _kis_json_payload_rate_limited(data):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("KIS rate limit in JSON body")
                    continue
                return data
            except ValueError as exc:
                last_error = exc
                time.sleep(0.35)
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                time.sleep(0.35)
        raise RuntimeError(f"{error_prefix}: retries exhausted ({last_error})") from last_error

    def get_cash_balance(self) -> int:
        """
        KIS: 주문가능현금 조회.
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": "005930",
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N",
        }
        tr_id = "VTTC8908R" if self.settings.is_paper_trading else "TTTC8908R"
        data = self._request_get_json(
            url, tr_id=tr_id, params=params, error_prefix="KIS CASH request failed"
        )
        rt = str(data.get("rt_cd", "0") or "0")
        if rt not in ("", "0"):
            msg = str(data.get("msg1", ""))
            raise RuntimeError(f"KIS CASH business error(rt_cd={rt}): {msg}")
        return int(data["output"]["ord_psbl_cash"])

    def get_domestic_balance_summary(self) -> Dict[str, object]:
        """
        KIS: 주식잔고조회 (요약 정보).

        Notes:
        - We intentionally keep a small, tolerant parser because field names can vary.
        - Returns a dict containing:
          - output2: account-level summary (first row if list)
          - raw: the full JSON payload (for logging / later tuning)
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = "VTTC8434R" if self.settings.is_paper_trading else "TTTC8434R"
        data = self._request_get_json(
            url,
            tr_id=tr_id,
            params=params,
            error_prefix="KIS balance summary request failed",
        )
        output2 = data.get("output2", {})
        if isinstance(output2, list):
            output2_row = output2[0] if output2 else {}
        elif isinstance(output2, dict):
            output2_row = output2
        else:
            output2_row = {}
        return {"output2": output2_row, "raw": data}

    def get_domestic_balance_positions(self) -> List[Dict[str, object]]:
        """
        KIS: 주식잔고조회 (종목별 보유 목록).

        Returns a list of dicts with at least:
          - symbol: str (6-digit code)
          - qty: int (holding quantity)
          - name: str (if provided by API; may be empty)
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": "",
        }
        tr_id = "VTTC8434R" if self.settings.is_paper_trading else "TTTC8434R"
        data = self._request_get_json(
            url,
            tr_id=tr_id,
            params=params,
            error_prefix="KIS balance positions request failed",
        )
        out = data.get("output1", [])
        if isinstance(out, dict):
            rows = [out]
        elif isinstance(out, list):
            rows = out
        else:
            rows = []

        positions: List[Dict[str, object]] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            symbol = str(r.get("pdno", "") or r.get("PDNO", "") or "").strip()
            if not symbol:
                symbol = str(r.get("prdt_code", "") or "").strip()
            qty_raw = str(
                r.get("hldg_qty", "")
                or r.get("HLDG_QTY", "")
                or r.get("hldg_qty", "")
                or "0"
            ).strip()
            try:
                qty = abs(int(float(qty_raw))) if qty_raw else 0
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            name = str(r.get("prdt_name", "") or r.get("PRDT_NAME", "") or "").strip()

            def _pv(*keys: str) -> float:
                for k in keys:
                    if k not in r or r[k] in (None, ""):
                        continue
                    try:
                        return float(str(r[k]).replace(",", "").strip())
                    except Exception:
                        continue
                return 0.0

            pchs_avg = _pv("pchs_avg_prvs", "PCHS_AVG_PRVS", "avg_prvs")
            pchs_amt = _pv("pchs_amt", "PCHS_AMT")
            if pchs_amt <= 0 and qty > 0 and pchs_avg > 0:
                pchs_amt = float(qty) * pchs_avg

            positions.append(
                {
                    "symbol": symbol,
                    "qty": qty,
                    "name": name,
                    "pchs_avg_prvs": pchs_avg,
                    "pchs_amt": pchs_amt,
                }
            )
        return positions

    def get_quote(self, symbol: str) -> Quote:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol}
        last_error: Optional[Exception] = None
        output: Optional[dict] = None
        got_quote = False
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers("FHKST01010100"),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400 and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError(_http_error_detail(resp))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if _kis_json_payload_rate_limited(body):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("quote rate limited")
                    continue
                output = body["output"]
                got_quote = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                else:
                    time.sleep(0.5)
        if not got_quote or output is None:
            raise RuntimeError(f"quote request failed for {symbol}: {last_error}") from last_error

        return Quote(
            symbol=symbol,
            current_price=_safe_abs_int(output.get("stck_prpr")),
            open_price=_safe_abs_int(output.get("stck_oprc")),
            volume=_safe_abs_int(output.get("acml_vol")),
            prev_high=_safe_abs_int(output.get("stck_hgpr")),
            prev_low=_safe_abs_int(output.get("stck_lwpr")),
            w52_high=_safe_abs_int(output.get("w52_hgpr")),
            w52_low=_safe_abs_int(output.get("w52_lwpr")),
        )

    def get_daily_prices(self, symbol: str, days: int = 6) -> List[Dict[str, int]]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_PERIOD_DIV_CODE": "D",
            "FID_ORG_ADJ_PRC": "1",
        }
        rows: List[Dict[str, str]] = []
        last_error: Optional[Exception] = None
        got_response = False
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers("FHKST01010400"),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400 and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError(_http_error_detail(resp))
                    continue
                resp.raise_for_status()
                body = resp.json()
                if _kis_json_payload_rate_limited(body):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("daily-price rate limited")
                    continue
                rows = body.get("output", [])[:days]
                got_response = True
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                else:
                    time.sleep(0.5)
        if not got_response:
            raise RuntimeError(f"daily-price request failed for {symbol}: {last_error}") from last_error

        parsed: List[Dict[str, int]] = []
        for r in rows:
            parsed.append(
                {
                    "close": abs(int(r["stck_clpr"])),
                    "high": abs(int(r["stck_hgpr"])),
                    "low": abs(int(r["stck_lwpr"])),
                    "volume": abs(int(r["acml_vol"])),
                }
            )
        return parsed

    def get_symbol_history(self, symbol: str, days: int = 130) -> SymbolHistory:
        """
        KIS: 국내주식기간별시세(일봉) — FHKST03010100

        52주 고저가(output1)와 일봉 OHLCV(output2)를 단일 호출 계열로 조회.
        days > 100이면 자동 페이징(backward).

        Returns:
            SymbolHistory.bars: 최신순 정렬, len <= days
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        # 충분한 달력일 버퍼: 거래일 1일 ≈ 1.5 달력일 + 여유
        calendar_days = int(days * 1.6) + 30
        kst_today = datetime.now(ZoneInfo("Asia/Seoul"))
        start_dt = kst_today - timedelta(days=calendar_days)
        start_yyyymmdd = start_dt.strftime("%Y%m%d")
        end_yyyymmdd = kst_today.strftime("%Y%m%d")

        all_bars: List[Dict[str, object]] = []
        w52_high = 0
        w52_low = 0
        w52_fetched = False

        for _page in range(15):  # 최대 15페이지 = 1500행 (252일 충분)
            params = {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": start_yyyymmdd,
                "FID_INPUT_DATE_2": end_yyyymmdd,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "1",
            }
            data = self._request_get_json(
                url,
                tr_id="FHKST03010100",
                params=params,
                error_prefix=f"symbol-history {symbol}",
            )

            # output1: 종목 기본 정보 (52주 고저가 등) — 첫 페이지에서만 수집
            if not w52_fetched:
                out1 = data.get("output1") or {}
                if isinstance(out1, list):
                    out1 = out1[0] if out1 else {}
                if isinstance(out1, dict):
                    w52_high = _safe_abs_int(out1.get("w52_hgpr"))
                    w52_low = _safe_abs_int(out1.get("w52_lwpr"))
                w52_fetched = True

            # output2: 일봉 배열
            out2 = data.get("output2", [])
            if isinstance(out2, dict):
                out2 = [out2]
            elif not isinstance(out2, list):
                out2 = []

            if not out2:
                break

            batch: List[Dict[str, object]] = []
            for r in out2:
                date_str = str(r.get("stck_bsop_date", "") or "").strip()
                if not date_str or len(date_str) != 8:
                    continue
                try:
                    batch.append({
                        "date": date_str,
                        "open": _safe_abs_int(r.get("stck_oprc")),
                        "high": _safe_abs_int(r.get("stck_hgpr")),
                        "low": _safe_abs_int(r.get("stck_lwpr")),
                        "close": _safe_abs_int(r.get("stck_clpr")),
                        "volume": _safe_abs_int(r.get("acml_vol")),
                    })
                except Exception:  # noqa: BLE001
                    continue

            all_bars.extend(batch)

            if len(all_bars) >= days:
                break

            # 페이징: 이번 배치의 가장 오래된 날짜 이전으로 end 이동
            dates_in_batch = [str(r["date"]) for r in batch if r.get("date")]
            if not dates_in_batch:
                break
            oldest_in_batch = min(dates_in_batch)
            if oldest_in_batch <= start_yyyymmdd:
                break

            oldest_dt = datetime.strptime(oldest_in_batch, "%Y%m%d")
            end_yyyymmdd = (oldest_dt - timedelta(days=1)).strftime("%Y%m%d")
            if end_yyyymmdd < start_yyyymmdd:
                break

            time.sleep(float(getattr(self.settings, "kis_min_request_interval_sec", 0.15)))

        # 중복 제거 + 최신순 정렬 + days 개수 제한
        seen: set = set()
        unique: List[Dict[str, object]] = []
        for r in all_bars:
            d = str(r.get("date", ""))
            if d and d not in seen:
                seen.add(d)
                unique.append(r)
        unique.sort(key=lambda x: str(x.get("date", "")), reverse=True)

        return SymbolHistory(
            symbol=symbol,
            w52_high=w52_high,
            w52_low=w52_low,
            bars=unique[:days],
        )

    def get_market_cap_list(self) -> List[Tuple[str, int]]:
        """
        KIS: KOSPI + KOSDAQ 시가총액 목록.

        Returns:
            List of (symbol, market_cap_億원) sorted by market cap descending.
            시총 API가 반환하는 상위 종목 범위 내에서만 유효.
        """
        merged: Dict[str, int] = {}
        for market in ("0001", "1001"):
            rows = self._get_market_cap_rows(fid_input_iscd=market)
            time.sleep(0.35)
            for row in rows:
                symbol = str(row.get("mksc_shrn_iscd", "") or "").strip()
                if not symbol:
                    continue
                cap = _safe_abs_int(row.get("stck_avls"))
                if cap <= 0:
                    continue
                merged[symbol] = max(merged.get(symbol, 0), cap)
        return sorted(merged.items(), key=lambda x: x[1], reverse=True)

    def get_market_cap_rankings(self) -> List[str]:
        """
        KIS 국내주식 시가총액 상위 조회.
        시장별(KOSPI/KOSDAQ) 결과를 합쳐 시총 내림차순으로 반환한다.
        """
        merged: Dict[str, int] = {}
        for market in ("0001", "1001"):
            rows = self._get_market_cap_rows(fid_input_iscd=market)
            time.sleep(0.35)
            for row in rows:
                symbol = row.get("mksc_shrn_iscd", "").strip()
                if not symbol:
                    continue
                cap = abs(int(row.get("stck_avls", "0") or 0))
                if cap <= 0:
                    continue
                merged[symbol] = max(merged.get(symbol, 0), cap)

        sorted_symbols = [
            item[0] for item in sorted(merged.items(), key=lambda x: x[1], reverse=True)
        ]
        return sorted_symbols

    def _get_market_cap_rows(self, fid_input_iscd: str) -> List[Dict[str, str]]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/ranking/market-cap"
        tr_id = "FHPST01740000"
        param_candidates: Tuple[Dict[str, str], ...] = (
            {
                "FID_COND_SCR_DIV_CODE": "20174",
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_DIV_CLS_CODE": "1",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "000000",
                "FID_INPUT_PRICE_1": "0",
                "FID_INPUT_PRICE_2": "0",
                "FID_VOL_CNT": "0",
                "FID_BLNG_CLS_CODE": "0",
                "FID_INPUT_DATE_1": "0",
                "FID_INPUT_ISCD": fid_input_iscd,
            },
            {
                "fid_cond_scr_div_code": "20174",
                "fid_cond_mrkt_div_code": "J",
                "fid_div_cls_code": "1",
                "fid_trgt_cls_code": "111111111",
                "fid_trgt_exls_cls_code": "000000",
                "fid_input_price_1": "0",
                "fid_input_price_2": "0",
                "fid_vol_cnt": "0",
                "fid_blng_cls_code": "0",
                "fid_input_date_1": "0",
                "fid_input_iscd": fid_input_iscd,
            },
        )

        last_error: Optional[Exception] = None
        for params in param_candidates:
            try:
                resp = self.session.get(
                    url,
                    headers=self._headers(tr_id),
                    params=params,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                resp.raise_for_status()
                data = resp.json()
                rt_cd = str(data.get("rt_cd", ""))
                msg1 = str(data.get("msg1", ""))
                if rt_cd not in {"", "0"}:
                    raise RuntimeError(f"KIS ranking error(rt_cd={rt_cd}): {msg1}")

                output = data.get("output", [])
                output1 = data.get("output1", [])
                rows = output if isinstance(output, list) else output1 if isinstance(output1, list) else []
                if rows:
                    return rows
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                continue

        raise RuntimeError(
            f"market-cap ranking request failed for market={fid_input_iscd}: {last_error}"
        )

    @staticmethod
    def _kis_row_opnd_yn(row: Dict[str, object]) -> str:
        """KIS 응답 키 대소문자·별칭 차이를 흡수합니다."""
        for k, v in row.items():
            if str(k).strip().upper() == "OPND_YN":
                return str(v or "").strip().upper()
        return ""

    def get_holiday_info(self, base_date_yyyymmdd: str) -> List[Dict[str, str]]:
        """
        KIS: 국내휴장일조회.
        참고: 단시간 다회 호출을 피하기 위해 호출부에서 캐시 사용 권장.
        """
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/quotations/chk-holiday"
        params = {
            "BASS_DT": base_date_yyyymmdd,
            "CTX_AREA_FK": "",
            "CTX_AREA_NK": "",
        }
        data = self._request_get_json(
            url,
            tr_id="CTCA0903R",
            params=params,
            error_prefix="KIS holiday request failed",
        )
        rt = str(data.get("rt_cd", "0") or "0")
        if rt not in ("", "0"):
            _log.warning(
                "KIS holiday rt_cd=%s msg=%s (BASS_DT=%s)",
                rt,
                data.get("msg1", ""),
                base_date_yyyymmdd,
            )
            return []

        rows: List[Dict[str, str]] = []
        for key in ("output", "output1", "output2"):
            block = data.get(key)
            if block is None or block == "":
                continue
            if isinstance(block, dict):
                rows.append(block)  # type: ignore[arg-type]
            elif isinstance(block, list):
                for item in block:
                    if isinstance(item, dict):
                        rows.append(item)  # type: ignore[arg-type]
        return rows

    def is_open_trading_day(self, base_date_yyyymmdd: str) -> Optional[bool]:
        """
        Returns:
            True/False if KIS responds with open-day flag,
            None if the payload cannot be interpreted.
        """
        rows = self.get_holiday_info(base_date_yyyymmdd=base_date_yyyymmdd)
        if not rows:
            return None
        row = rows[0]
        open_flag = self._kis_row_opnd_yn(row)
        if open_flag == "Y":
            return True
        if open_flag == "N":
            return False
        return None

    def place_limit_buy(self, symbol: str, qty: int, price: int) -> Dict[str, str]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": "00",
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        tr_id = "VTTC0802U" if self.settings.is_paper_trading else "TTTC0802U"
        return self._post_order(url, body, tr_id)

    def place_market_sell(self, symbol: str, qty: int) -> Dict[str, str]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        body = {
            "CANO": self.settings.cano,
            "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
            "PDNO": symbol,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
        }
        tr_id = "VTTC0801U" if self.settings.is_paper_trading else "TTTC0801U"
        return self._post_order(url, body, tr_id)

    def _post_order(self, url: str, body: Dict[str, str], tr_id: str) -> Dict[str, str]:
        last_error: Optional[Exception] = None
        for _ in range(self._kis_retry_max()):
            try:
                resp = self.session.post(
                    url,
                    headers=self._headers(tr_id),
                    json=body,
                    timeout=self.settings.request_timeout_sec,
                )
                self._update_server_time_offset_from_response(resp)
                if resp.status_code >= 400 and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError(_http_error_detail(resp))
                    continue
                resp.raise_for_status()
                data = resp.json()
                if _kis_json_payload_rate_limited(data):
                    time.sleep(self._kis_rate_sleep())
                    last_error = RuntimeError("order rate limited")
                    continue
                return {
                    "rt_cd": data.get("rt_cd", ""),
                    "msg1": data.get("msg1", ""),
                    "ord_no": data.get("output", {}).get("ODNO", ""),
                }
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                resp = getattr(exc, "response", None)
                if resp is not None and _response_is_rate_limited(resp):
                    time.sleep(self._kis_rate_sleep())
                else:
                    time.sleep(0.7)
        raise RuntimeError(f"order failed after retry: {last_error}") from last_error

    def _fetch_inquire_daily_ccld(self, start_yyyymmdd: str, end_yyyymmdd: str, tr_id: str) -> List[Dict[str, object]]:
        url = f"{self.settings.base_url}/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        all_rows: List[Dict[str, object]] = []
        fk, nk = "", ""
        page_count = 0
        while True:
            page_count += 1
            if page_count > 500:
                raise RuntimeError("KIS daily ccld: pagination limit exceeded")
            params = {
                "CANO": self.settings.cano,
                "ACNT_PRDT_CD": self.settings.acnt_prdt_cd,
                "INQR_STRT_DT": start_yyyymmdd,
                "INQR_END_DT": end_yyyymmdd,
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": "",
                "CCLD_DVSN": "01",
                "INQR_DVSN": "01",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": fk,
                "CTX_AREA_NK100": nk,
            }
            data: Optional[dict] = None
            resp: Optional[requests.Response] = None
            last_err: Optional[Exception] = None
            for _ in range(self._kis_retry_max()):
                try:
                    self.ensure_token()
                    self._pace_api()
                    resp = self.session.get(
                        url,
                        headers=self._headers(tr_id),
                        params=params,
                        timeout=self.settings.request_timeout_sec,
                    )
                    self._update_server_time_offset_from_response(resp)
                    if resp.status_code >= 400 and _response_is_rate_limited(resp):
                        time.sleep(self._kis_rate_sleep())
                        last_err = RuntimeError("rate limited")
                        continue
                    if resp.status_code >= 400:
                        detail = _http_error_detail(resp)
                        raise RuntimeError(f"KIS daily ccld HTTP error: {detail}")
                    data = resp.json()
                    if _kis_json_payload_rate_limited(data):
                        time.sleep(self._kis_rate_sleep())
                        last_err = RuntimeError("rate limited json")
                        continue
                    break
                except RuntimeError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_err = exc
                    time.sleep(0.35)
            if data is None or resp is None:
                raise RuntimeError(f"KIS daily ccld failed: {last_err}") from last_err

            rt = str(data.get("rt_cd", "0") or "0")
            if rt not in ("", "0"):
                msg = str(data.get("msg1", ""))
                raise RuntimeError(f"KIS daily ccld biz error(rt_cd={rt}): {msg}")
            rows = data.get("output1", [])
            if isinstance(rows, dict):
                rows = [rows]
            elif not isinstance(rows, list):
                rows = []
            all_rows.extend(rows)
            fk = str(data.get("ctx_area_fk100") or data.get("CTX_AREA_FK100") or "").strip()
            nk = str(data.get("ctx_area_nk100") or data.get("CTX_AREA_NK100") or "").strip()
            tr_cont = _response_header_ci(resp, "tr_cont").upper()
            if tr_cont in ("M", "F") and (fk or nk):
                time.sleep(0.25)
                continue
            break
        return all_rows

    def get_daily_order_executions(self, start_yyyymmdd: str, end_yyyymmdd: str) -> List[Dict[str, object]]:
        """
        KIS: 주식일별주문체결조회 — 우선 TTTC0081R(3개월 이내), 0건이면 TTTC8001R로 재조회.

        체결만(CCLD_DVSN=01), 정순(INQR_DVSN=01). EXCG_ID는 보내지 않음(거래소 필터 회피).
        """
        tid = "VTTC0081R" if self.settings.is_paper_trading else "TTTC0081R"
        rows = self._fetch_inquire_daily_ccld(start_yyyymmdd, end_yyyymmdd, tid)
        if not rows:
            _log.warning("KIS 일별체결 TR=%s 0건; TR=8001R로 재조회", tid)
            tid2 = "VTTC8001R" if self.settings.is_paper_trading else "TTTC8001R"
            rows = self._fetch_inquire_daily_ccld(start_yyyymmdd, end_yyyymmdd, tid2)
        return rows


def _response_header_ci(resp: requests.Response, name: str) -> str:
    want = name.lower()
    for k, v in resp.headers.items():
        if k.lower() == want:
            return (v or "").strip()
    return ""


def _http_error_detail(resp: requests.Response) -> str:
    status = getattr(resp, "status_code", None)
    try:
        body_ct = (resp.headers or {}).get("content-type", "")
    except Exception:
        body_ct = ""
    preview = ""
    try:
        txt = resp.text or ""
        txt = txt.replace("\r", " ").replace("\n", " ").strip()
        preview = txt[:400]
    except Exception:
        preview = ""
    return f"status={status} content_type={body_ct!s} body_preview={preview!s}"
