"""
Universe builder: 장 시작 전 배치로 당일 감시 목록(UniverseCache)을 생성한다.

Pipeline:
  1. 시총 목록 수집 (Naver primary → KIS fallback)
  2. 1차 필터: 시총, ETF, 우선주
  3. 종목별 히스토리 수집 (Naver primary → KIS fallback)
  4. RS 필터: 6개월 수익률 상위 10%
  5. 피처 계산: w52_high, vol_ma20, w52_hit_60d, w52_hit_10d
  6. 2차 필터: 전략 모드별 조건
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from config.settings import Settings
from core.api_client import KISApiClient, SymbolHistory
from core.universe_cache import CachedSymbol, MIN_VOL_MA20, UniverseCache

_log = logging.getLogger("moa")

RS_LOOKBACK_DAYS = 126   # 6개월 거래일 수 (대략)
HISTORY_DAYS = 135       # KIS fallback용 (126 + 여유 9일)
NAVER_HISTORY_PAGES = 30 # Naver primary용: 30페이지 × 10행 = 300봉 ≈ 1.2년 (52주 고가 커버)

_ETF_NAME_PREFIXES: Tuple[str, ...] = (
    "KODEX", "TIGER", "KBSTAR", "HANARO", "ARIRANG", "TREX",
    "KOSEF", "SOL", "ACE", "TIMEFOLIO", "PLUS", "SMART",
    "FOCUS", "WOORI",
)
_ETF_NAME_KEYWORDS: Tuple[str, ...] = (
    "ETF", "리츠", "인프라", "채권", "혼합", "머니마켓", "국채", "회사채",
)


# ---------------------------------------------------------------------------
# Pure utility functions — I/O 없음, 독립 테스트 가능
# ---------------------------------------------------------------------------

def is_preferred_stock(symbol: str) -> bool:
    """
    우선주 여부: 6자리 숫자 코드 마지막 자리가 0이 아닌 경우.
    예: 005935(삼성전자우) → True, 005930(삼성전자) → False
    """
    return len(symbol) == 6 and symbol.isdigit() and symbol[-1] != "0"


def is_etf_by_name(name: str) -> bool:
    """종목명 기반 ETF 여부 판별."""
    upper = name.upper()
    for kw in _ETF_NAME_PREFIXES:
        if upper.startswith(kw.upper()):
            return True
    for kw in _ETF_NAME_KEYWORDS:
        if kw in name:
            return True
    return False


def calc_vol_ma(bars: List[Dict], days: int = 20) -> int:
    """최근 days일 평균 거래량. bars는 최신순(newest-first)."""
    vols = [int(b.get("volume", 0)) for b in bars[:days]]
    return int(sum(vols) / len(vols)) if vols else 0


def calc_w52_hit_count(bars: List[Dict], w52_high: int, lookback: int) -> int:
    """
    lookback일 내 52주 신고가 터치(daily high >= w52_high) 횟수.
    bars는 최신순(newest-first).
    """
    if w52_high <= 0:
        return 0
    return sum(1 for b in bars[:lookback] if int(b.get("high", 0)) >= w52_high)


def calc_rs_return(bars: List[Dict], lookback: int = RS_LOOKBACK_DAYS) -> Optional[float]:
    """
    6개월 주가수익률: (close[0] / close[lookback] - 1).
    bars: 최신순(newest-first). 데이터 부족 또는 종가 0이면 None 반환.
    """
    if len(bars) <= lookback:
        return None
    recent = int(bars[0].get("close", 0))
    base = int(bars[lookback].get("close", 0))
    if base <= 0 or recent <= 0:
        return None
    return (recent / base) - 1.0


def compute_rs_top_pct(
    symbol_returns: Dict[str, float],
    top_pct: float,
) -> List[str]:
    """
    수익률 딕셔너리에서 상위 top_pct% 종목 코드 리스트 반환.
    top_pct=0.10 → 상위 10%.
    """
    if not symbol_returns:
        return []
    ranked = sorted(symbol_returns.items(), key=lambda x: x[1], reverse=True)
    cutoff = max(1, int(len(ranked) * top_pct))
    return [sym for sym, _ in ranked[:cutoff]]


def build_features(
    history: SymbolHistory,
    fresh_days: int,
    cont_days: int,
) -> Optional[CachedSymbol]:
    """
    SymbolHistory에서 CachedSymbol 피처를 계산.

    Args:
        history: get_symbol_history() 결과
        fresh_days: 전략1용 신고가 터치 조회 기간 (settings.w52_fresh_days)
        cont_days: 전략2용 신고가 터치 조회 기간 (settings.w52_cont_lookback_days)

    Returns:
        CachedSymbol, 또는 w52_high=0, bars 부족, vol_ma20 미달 시 None.
    """
    if history.w52_high <= 0:
        return None
    if len(history.bars) < 20:
        return None

    vol_ma20 = calc_vol_ma(history.bars, days=20)
    if vol_ma20 < MIN_VOL_MA20:
        return None

    return CachedSymbol(
        w52_high=history.w52_high,
        vol_ma20=vol_ma20,
        w52_hit_60d=calc_w52_hit_count(history.bars, history.w52_high, lookback=fresh_days),
        w52_hit_10d=calc_w52_hit_count(history.bars, history.w52_high, lookback=cont_days),
    )


def apply_second_filter(
    features: Dict[str, CachedSymbol],
    strategy_mode: int,
    fresh_days: int,
    cont_min_hits: int,
) -> Dict[str, CachedSymbol]:
    """
    전략 모드별 2차 필터 적용.

    전략1: w52_hit_60d == 0  (최근 fresh_days일 내 신고가 터치 없음 → 최초 돌파 후보)
    전략2: w52_hit_10d >= cont_min_hits  (최근 cont_days일 내 반복 신고가 → 추세 지속)
    """
    result: Dict[str, CachedSymbol] = {}
    for sym, feat in features.items():
        if strategy_mode == 1:
            if feat.w52_hit_60d == 0:
                result[sym] = feat
        elif strategy_mode == 2:
            if feat.w52_hit_10d >= cont_min_hits:
                result[sym] = feat
    return result


# ---------------------------------------------------------------------------
# UniverseBuilder
# ---------------------------------------------------------------------------

class UniverseBuilder:
    """
    장 시작 전 배치: 당일 감시 목록(UniverseCache)을 빌드한다.

    사용 예::

        builder = UniverseBuilder(api=api, settings=settings)
        cache = builder.build()
        save_cache(cache_path(Path("data"), cache.date_kst), cache)
    """

    def __init__(
        self,
        api: KISApiClient,
        settings: Settings,
        symbol_names: Optional[Dict[str, str]] = None,
    ) -> None:
        self.api = api
        self.settings = settings
        # symbol_names: code→name 맵 (ETF 이름 필터 1순위).
        self.symbol_names: Dict[str, str] = symbol_names or {}
        # _cap_list_names: 시총 목록 수집 시 부산물로 얻은 종목명 (ETF 이름 필터 fallback).
        self._cap_list_names: Dict[str, str] = {}
        # Naver HTTP 세션 (lazy init, 히스토리 수집 전체에 재사용).
        self._naver_session: Optional[requests.Session] = None

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(self, now_kst: Optional[datetime] = None) -> UniverseCache:
        """전체 파이프라인 실행 후 UniverseCache 반환."""
        now_kst = now_kst or datetime.now(ZoneInfo("Asia/Seoul"))
        date_kst = now_kst.strftime("%Y%m%d")

        _log.info(
            "[UB] 유니버스 빌드 시작 date=%s strategy=%s",
            date_kst, self.settings.strategy_mode,
        )

        cap_list = self._fetch_cap_list()
        _log.info("[UB] Step1 시총 목록: %d종목", len(cap_list))

        filtered = self._apply_first_filter(cap_list)
        _log.info("[UB] Step2 1차 필터 후: %d종목", len(filtered))

        histories = self._fetch_histories(filtered)
        _log.info("[UB] Step3 히스토리 수집: %d/%d", len(histories), len(filtered))

        rs_passed, symbol_returns = self._apply_rs_filter(filtered, histories)
        _log.info(
            "[UB] Step4 RS 필터: 계산가능=%d → 상위%d%%=%d종목",
            len(symbol_returns),
            int(self.settings.rs_top_pct * 100),
            len(rs_passed),
        )

        features = self._compute_features(rs_passed, histories)
        _log.info("[UB] Step5 피처 계산: %d종목", len(features))

        final = apply_second_filter(
            features,
            strategy_mode=self.settings.strategy_mode,
            fresh_days=self.settings.w52_fresh_days,
            cont_min_hits=self.settings.w52_cont_min_hits,
        )
        _log.info("[UB] Step6 최종 감시 목록: %d종목", len(final))

        return UniverseCache(
            date_kst=date_kst,
            strategy_mode=self.settings.strategy_mode,
            created_at_iso=now_kst.isoformat(),
            symbols=final,
        )

    # ------------------------------------------------------------------
    # Private steps
    # ------------------------------------------------------------------

    def _fetch_cap_list(self) -> List[Tuple[str, int]]:
        """시총 목록 조회. Naver primary → KIS fallback.

        부산물로 self._cap_list_names({symbol: name})를 갱신해
        symbol_names가 없을 때 ETF 이름 필터 fallback으로 사용한다.
        """
        result = self._fetch_cap_list_naver()
        if result:
            return result

        # KIS fallback (Naver 실패 시)
        _log.info("[UB] Naver 시총 없음. KIS fallback 시도")
        try:
            result = self.api.get_market_cap_list()
            self._cap_list_names = dict(getattr(self.api, "_last_cap_list_names", {}))
            return result
        except Exception as exc:
            _log.warning("[UB] KIS 시총 조회도 실패: %s", exc)
            return []

    def _fetch_cap_list_naver(self) -> List[Tuple[str, int]]:
        """Naver 시총 페이지에서 (symbol, cap_억원) 수집.
        실패(네트워크·파싱 오류) 시 [] 반환.
        """
        try:
            from core.naver_universe import fetch_market_cap_list as naver_cap
            cap_list, naver_names = naver_cap(delay_sec=self.settings.naver_http_delay_sec)
            for sym, name in naver_names.items():
                self._cap_list_names.setdefault(sym, name)
            return cap_list
        except Exception as exc:
            _log.warning("[UB] Naver 시총 조회 실패: %s", exc)
            return []

    def _apply_first_filter(self, cap_list: List[Tuple[str, int]]) -> List[str]:
        """시총·우선주·ETF 필터 후 종목코드 리스트 반환.

        ETF 이름 필터 우선순위:
          1. self.symbol_names (Naver 종목 마스터 — 가장 완전한 목록)
          2. self._cap_list_names (KIS/Naver 시총 API 부산물 — 마스터 없을 때 fallback)
        """
        min_cap = self.settings.min_market_cap_billion
        # 이름 출처: 마스터 우선, 없으면 시총 API 부산물
        effective_names = self.symbol_names if self.symbol_names else self._cap_list_names
        result: List[str] = []
        n_cap = n_pref = n_etf = 0

        for symbol, cap in cap_list:
            if not symbol or len(symbol) != 6 or not symbol.isdigit():
                continue
            if cap < min_cap:
                n_cap += 1
                continue
            if not self.settings.include_preferred and is_preferred_stock(symbol):
                n_pref += 1
                continue
            if not self.settings.include_etf and effective_names:
                name = effective_names.get(symbol, "")
                if name and is_etf_by_name(name):
                    n_etf += 1
                    continue
            result.append(symbol)

        _log.info(
            "[UB] 1차 필터 제외 — 시총(%d) 우선주(%d) ETF(%d)", n_cap, n_pref, n_etf,
        )
        return result

    def _get_naver_session(self) -> requests.Session:
        """Naver HTTP 세션 lazy init (히스토리 수집 전체에 재사용)."""
        if self._naver_session is None:
            from core.naver_universe import _UA
            self._naver_session = requests.Session()
            self._naver_session.headers.update(_UA)
        return self._naver_session

    def _fetch_history_naver(
        self,
        symbol: str,
        session: requests.Session,
    ) -> Optional[SymbolHistory]:
        """Naver sise_day에서 히스토리 조회. 실패 시 None."""
        try:
            from core.naver_universe import fetch_symbol_history_naver
            return fetch_symbol_history_naver(  # type: ignore[return-value]
                symbol=symbol,
                pages=NAVER_HISTORY_PAGES,
                delay_sec=self.settings.naver_http_delay_sec,
                session=session,
            )
        except Exception as exc:
            _log.debug("[UB] Naver 히스토리 실패 %s: %s", symbol, exc)
            return None

    def _fetch_histories(self, symbols: List[str]) -> Dict[str, SymbolHistory]:
        """각 종목 히스토리(OHLCV + 52주 고저가) 일괄 수집.
        Naver primary → 종목별 KIS fallback.
        """
        histories: Dict[str, SymbolHistory] = {}
        total = len(symbols)
        log_every = max(1, total // 10)
        session = self._get_naver_session()

        for idx, symbol in enumerate(symbols):
            if idx % log_every == 0:
                _log.info("[UB] 히스토리 수집 %d/%d …", idx, total)

            hist: Optional[SymbolHistory] = self._fetch_history_naver(symbol, session)

            if hist is None:
                try:
                    kis = self.api.get_symbol_history(symbol, days=HISTORY_DAYS)
                    hist = kis if kis.bars else None
                except Exception as exc:
                    _log.debug("[UB] KIS 히스토리 실패 %s: %s", symbol, exc)

            if hist is not None:
                histories[symbol] = hist

        return histories

    def _apply_rs_filter(
        self,
        symbols: List[str],
        histories: Dict[str, SymbolHistory],
    ) -> Tuple[List[str], Dict[str, float]]:
        """6개월 수익률 계산 후 상위 rs_top_pct% 종목 반환."""
        returns: Dict[str, float] = {}
        for sym in symbols:
            hist = histories.get(sym)
            if hist is None:
                continue
            rs = calc_rs_return(hist.bars, lookback=RS_LOOKBACK_DAYS)
            if rs is not None:
                returns[sym] = rs

        passed = compute_rs_top_pct(returns, top_pct=self.settings.rs_top_pct)
        return passed, returns

    def _compute_features(
        self,
        symbols: List[str],
        histories: Dict[str, SymbolHistory],
    ) -> Dict[str, CachedSymbol]:
        """각 종목 CachedSymbol 피처 계산."""
        features: Dict[str, CachedSymbol] = {}
        for sym in symbols:
            hist = histories.get(sym)
            if hist is None:
                continue
            feat = build_features(
                hist,
                fresh_days=self.settings.w52_fresh_days,
                cont_days=self.settings.w52_cont_lookback_days,
            )
            if feat is not None:
                features[sym] = feat
        return features
