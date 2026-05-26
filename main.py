from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.logger import TradeLogger
from core.naver_symbol_master import load_or_refresh_symbol_master
from core.order import OrderManager
from core.strategy import W52HighStrategy
from core.trading_day import should_run_bot_today_kst
from core.universe_builder import UniverseBuilder
from core.universe_cache import (
    CachedSymbol,
    MIN_VOL_MA20,
    UniverseCache,
    cache_path,
    load_cache,
    resolve_cache_path,
    save_cache,
    today_kst_yyyymmdd,
)


_REPO_ROOT = Path(__file__).resolve().parent


class MoaRunner:
    def __init__(self) -> None:
        settings.validate()
        self.api = KISApiClient(settings=settings)
        self.logger = TradeLogger(log_dir=settings.log_dir)
        self.order = OrderManager(api=self.api, settings=settings)
        self.strategy = W52HighStrategy(trailing_stop_pct=settings.trailing_stop_pct)

        self.watchlist: List[str] = []         # 당일 감시 대상 (매수 후보)
        self.universe_symbols: Dict[str, CachedSymbol] = {}
        self.symbol_strategies: Dict[str, int] = {}  # symbol -> 1 or 2
        self.positions: List[str] = []         # 현재 보유 종목

        self.ordered_symbols_today: set[str] = set()
        self.buy_orders_today: int = 0

        # 가상 매매 기록 (sim_mode=True 일 때)
        self._paper_trades: List[Dict] = []

        # 09:00 현금 스냅샷
        self.cash_snapshot_total: Optional[int] = None
        self.per_symbol_budget: Optional[int] = None
        self.cash_snapshot_done: bool = False
        self.cash_snapshot_failed: bool = False
        self._next_cash_retry_ts: float = 0.0
        self.cash_retry_interval_sec: float = 7.0

        self._last_heartbeat_ts: float = 0.0
        self._hb_cycles: int = 0
        self._hb_signals: int = 0
        self._hb_buys: int = 0
        self._hb_quote_err: int = 0
        self._should_stop: bool = False
        self._did_prepare_ymd: str = ""
        self._did_result_csv_ymd: str = ""
        self._last_monitor_ts: float = 0.0

    def run(self) -> None:
        self._configure_console_utf8()

        now = self._now_kst()
        ymd = now.strftime("%Y%m%d")
        ok_day, holiday_msg = should_run_bot_today_kst(ymd, settings)
        if not ok_day:
            print(holiday_msg)
            self.logger.info(holiday_msg)
            return

        self.logger.info(
            f"moa started. mode={'paper' if settings.is_paper_trading else 'live'} "
            f"strategies=1+2 dual"
        )

        hhmm = now.strftime("%H:%M")
        if hhmm >= "15:30":
            self.logger.info("장 종료 이후 시간입니다.")
            return

        self.prepare_universe()
        self._did_prepare_ymd = ymd

        while not self._should_stop:
            self._tick()
            time.sleep(0.25)

        self.logger.info("moa stopped.")

    def _now_kst(self) -> datetime:
        return datetime.now(ZoneInfo("Asia/Seoul"))

    def _tick(self) -> None:
        now = self._now_kst()
        ymd = now.strftime("%Y%m%d")
        hhmm = now.strftime("%H:%M")

        if hhmm >= settings.result_write_hhmm:
            if self._did_result_csv_ymd != ymd:
                self._write_daily_result(ymd)
                self._did_result_csv_ymd = ymd

        if hhmm >= settings.shutdown_hhmm:
            self._should_stop = True
            return

        now_ts = time.time()
        if now_ts - self._last_monitor_ts >= settings.poll_interval_sec:
            self._last_monitor_ts = now_ts
            self._monitor_watchlist()
            self._monitor_positions()

    def prepare_universe(self) -> None:
        """
        당일 감시 목록 준비 (전략1+2 동시).
        history_cache 증분 갱신 후 dual 유니버스 생성/로드.
        """
        self.logger.info("유니버스 준비 중 (전략1+2)...")

        cache_date = today_kst_yyyymmdd(self._now_kst())
        data_dir = Path("data")
        path_s1 = resolve_cache_path(data_dir, cache_date, 1)
        path_s2 = resolve_cache_path(data_dir, cache_date, 2)
        c1 = load_cache(path_s1, strategy_mode=1)
        c2 = load_cache(path_s2, strategy_mode=2)

        if (
            c1 is not None
            and c2 is not None
            and c1.date_kst == cache_date
            and c2.date_kst == cache_date
        ):
            self._register_universe_caches(c1, c2)
            self.logger.info(
                f"유니버스 캐시 로드: s1={len(c1.symbols)} s2={len(c2.symbols)} "
                f"watchlist={len(self.watchlist)}"
            )
        else:
            self.logger.info("유니버스 캐시 없음/불완전. dual 빌드 시작 ...")
            try:
                from core.history_cache import HistoryCacheStore
                from core.universe_xlsx import append_universe_xlsx_rows

                symbol_names = load_or_refresh_symbol_master(
                    settings.symbol_master_path,
                    auto_refresh=settings.symbol_master_auto_refresh,
                    max_age_days=settings.symbol_master_max_age_days,
                    delay_sec=settings.naver_http_delay_sec,
                )
                history_store = HistoryCacheStore(
                    settings.history_cache_dir,
                    delay_sec=settings.naver_http_delay_sec,
                    jitter_sec=settings.naver_request_jitter_sec,
                    batch_size=settings.naver_batch_size,
                    batch_pause_sec=settings.naver_batch_pause_sec,
                    api=self.api,
                )
                builder = UniverseBuilder(
                    api=self.api,
                    settings=settings,
                    symbol_names=symbol_names,
                    history_store=history_store,
                )
                result = builder.build_dual(now_kst=self._now_kst(), bootstrap=False)
                save_cache(cache_path(data_dir, cache_date, 1), result.cache_s1)
                save_cache(cache_path(data_dir, cache_date, 2), result.cache_s2)
                append_universe_xlsx_rows(
                    settings.universe_xlsx_path,
                    result.xlsx_rows,
                    symbol_names=symbol_names,
                )
                self._register_universe_caches(result.cache_s1, result.cache_s2)
                self.logger.info(
                    f"유니버스 dual 빌드 완료: s1={len(result.cache_s1.symbols)} "
                    f"s2={len(result.cache_s2.symbols)} watchlist={len(self.watchlist)} "
                    f"xlsx={settings.universe_xlsx_path.name}"
                )
            except Exception as exc:
                self.logger.error(f"유니버스 빌드 실패: {exc}. 감시 목록 비어 있습니다.")
                self.watchlist = []
                self.universe_symbols = {}
                self.symbol_strategies = {}

        self._apply_open_positions(self.universe_symbols)

        self.logger.info(
            f"감시 준비 완료. watchlist={len(self.watchlist)} "
            f"s1={sum(1 for v in self.symbol_strategies.values() if v == 1)} "
            f"s2={sum(1 for v in self.symbol_strategies.values() if v == 2)} "
            f"({settings.monitor_start_hhmm}~{settings.monitor_end_hhmm} KST)"
        )

    def _register_universe_caches(
        self,
        cache_s1: UniverseCache,
        cache_s2: UniverseCache,
    ) -> None:
        self.strategy.symbol_state.clear()
        self.universe_symbols = {}
        self.symbol_strategies = {}

        for symbol, feat in cache_s1.symbols.items():
            self.strategy.register(
                symbol=symbol,
                w52_high=feat.w52_high,
                vol_ma20=feat.vol_ma20,
                strategy_mode=1,
            )
            self.universe_symbols[symbol] = feat
            self.symbol_strategies[symbol] = 1

        for symbol, feat in cache_s2.symbols.items():
            self.strategy.register(
                symbol=symbol,
                w52_high=feat.w52_high,
                vol_ma20=feat.vol_ma20,
                strategy_mode=2,
            )
            self.universe_symbols[symbol] = feat
            self.symbol_strategies[symbol] = 2

        self.watchlist = self.strategy.watchlist_symbols()

    def _load_open_positions(self) -> Dict[str, object]:
        from core.open_positions import (
            load_open_positions_from_kis,
            load_open_positions_from_trades_csv,
        )

        try:
            if settings.sim_mode:
                return load_open_positions_from_trades_csv(self.logger.trade_csv)
            return load_open_positions_from_kis(self.api)
        except Exception as exc:
            self.logger.error(f"보유 종목 조회 실패: {exc}")
            return {}

    def _apply_open_positions(self, universe_symbols: Dict[str, CachedSymbol]) -> None:
        """기존 보유 종목은 매수 감시에서 제외하고 트레일링 스탑만 추적."""
        open_pos = self._load_open_positions()
        if not open_pos:
            return

        before = len(self.watchlist)
        held_syms: List[str] = []
        for sym, pos in sorted(open_pos.items()):
            feat = universe_symbols.get(sym)
            w52 = feat.w52_high if feat else 0
            vol = feat.vol_ma20 if feat else MIN_VOL_MA20
            self.strategy.apply_open_position(
                sym,
                pos.avg_buy_price,
                pos.qty,
                w52_high=w52,
                vol_ma20=vol,
            )
            held_syms.append(sym)
            if sym not in self.positions:
                self.positions.append(sym)

        held_set = set(held_syms)
        self.watchlist = [s for s in self.watchlist if s not in held_set]
        self.logger.info(
            f"보유 종목 매수 감시 제외: {len(held_set)}종목 {held_syms} "
            f"(watchlist {before}→{len(self.watchlist)})"
        )

    def _monitor_watchlist(self) -> None:
        """장중 감시: 매수 후보 종목의 52주 신고가 터치 체크."""
        hhmm = self._now_kst().strftime("%H:%M")
        if hhmm < settings.monitor_start_hhmm or hhmm > settings.monitor_end_hhmm:
            self._maybe_heartbeat(
                f"대기중... now={hhmm} KST watchlist={len(self.watchlist)} "
                f"orders={self.buy_orders_today}/{settings.max_positions}"
            )
            return

        if not self.watchlist:
            self._maybe_heartbeat(f"감시대상 없음. now={hhmm} KST")
            return

        # 실매매 모드: 현금 스냅샷 필요 / 시뮬 모드: 스킵
        budget = 0
        if not settings.sim_mode:
            if not self._ensure_cash_snapshot():
                return
            budget = int(self.per_symbol_budget or 0)
            if budget <= 0:
                self.logger.error("per_symbol_budget 이상. 감시만 진행합니다.")
                return

        can_buy = self.buy_orders_today < settings.max_positions

        self._hb_cycles += 1
        for symbol in self.strategy.watchlist_symbols():
            if symbol in self.ordered_symbols_today:
                continue
            try:
                quote = self.api.get_quote(symbol)
            except Exception as exc:
                self.logger.error(f"QUOTE 실패 {symbol}: {exc}")
                self._hb_quote_err += 1
                continue

            signal = self.strategy.on_quote(
                symbol=symbol,
                current_price=quote.current_price,
                current_volume=quote.volume,
            )
            if signal is None:
                continue

            self._hb_signals += 1
            self.logger.log_signal({
                "symbol": symbol,
                "breakout_price": signal.entry_price,
                "reason": signal.reason,
                "action": "SKIP_FULL_CAP" if not can_buy else (
                    "SIM_BUY" if settings.sim_mode else "BUY_ATTEMPT"
                ),
                "note": f"strategy={signal.strategy_mode or self.symbol_strategies.get(symbol, 0)}",
            })

            if not can_buy:
                continue

            if not settings.sim_mode and signal.entry_price > budget:
                self.logger.log_signal({
                    "symbol": symbol,
                    "breakout_price": signal.entry_price,
                    "reason": signal.reason,
                    "action": "SKIP_HIGH_PRICE",
                    "note": "1주가 예산 초과",
                })
                continue

            if settings.sim_mode:
                qty = 1
                ord_no = "SIM"
            else:
                try:
                    result = self.order.place_breakout_buy_with_budget(
                        symbol=symbol,
                        per_symbol_budget=budget,
                        breakout_price=signal.entry_price,
                    )
                except Exception as exc:
                    self.logger.error(f"BUY 실패 {symbol}: {exc}")
                    continue

                ord_no = str(result.get("ord_no", "") or "")
                if not ord_no:
                    self.logger.error(f"BUY ACK ord_no 없음: {symbol}. 미계수.")
                    continue

                qty = self.order.calc_buy_qty_with_budget(
                    per_symbol_budget=budget, breakout_price=signal.entry_price
                )

            self.strategy.register_position(
                symbol=symbol, buy_price=signal.entry_price, qty=qty
            )
            self.ordered_symbols_today.add(symbol)
            self.buy_orders_today += 1
            if symbol not in self.positions:
                self.positions.append(symbol)

            if settings.sim_mode:
                self._paper_trades.append({
                    "ts": self._now_kst(),
                    "side": "BUY",
                    "symbol": symbol,
                    "qty": qty,
                    "price": signal.entry_price,
                    "strategy_mode": signal.strategy_mode or self.symbol_strategies.get(symbol, 0),
                })

            self.logger.log_trade({
                "symbol": symbol,
                "side": "BUY",
                "qty": qty,
                "price": signal.entry_price,
                "reason": signal.reason,
                "order_id": ord_no,
                "fee": "",
                "tax": "",
                "cash_psbl": "",
                "balance_tot_asset": "",
                "balance_dnca": "",
                "balance_json": "",
                "pnl_cash_delta": "",
            })
            label = "가상매수" if settings.sim_mode else "매수"
            strat = signal.strategy_mode or self.symbol_strategies.get(symbol, 0)
            self.logger.info(
                f"[{label}] {symbol} price={signal.entry_price} qty={qty} strategy={strat}"
                + (f" ord_no={ord_no}" if not settings.sim_mode else "")
            )
            self._hb_buys += 1
            if self.buy_orders_today >= settings.max_positions:
                can_buy = False
            time.sleep(0.15)

        self._maybe_heartbeat(
            f"감시중... now={hhmm} KST watchlist={len(self.watchlist)} "
            f"orders={self.buy_orders_today}/{settings.max_positions} "
            f"(cycles={self._hb_cycles} signals={self._hb_signals} "
            f"buys={self._hb_buys} quote_err={self._hb_quote_err})"
        )

    def _monitor_positions(self) -> None:
        """보유 포지션 트레일링 스탑 체크."""
        held = self.strategy.held_symbols()
        for symbol in held:
            try:
                quote = self.api.get_quote(symbol)
            except Exception as exc:
                self.logger.error(f"POSITION QUOTE 실패 {symbol}: {exc}")
                continue

            sell_signal = self.strategy.on_position_quote(
                symbol=symbol, current_price=quote.current_price
            )
            if sell_signal is None:
                continue

            if settings.sim_mode:
                ord_no = "SIM"
                self._paper_trades.append({
                    "ts": self._now_kst(),
                    "side": "SELL",
                    "symbol": symbol,
                    "qty": sell_signal.qty,
                    "price": quote.current_price,
                })
            else:
                try:
                    result = self.api.place_market_sell(symbol=symbol, qty=sell_signal.qty)
                except Exception as exc:
                    self.logger.error(f"SELL 실패 {symbol}: {exc}")
                    continue
                ord_no = str(result.get("ord_no", "") or "")

            self.logger.log_trade({
                "symbol": symbol,
                "side": "SELL",
                "qty": sell_signal.qty,
                "price": quote.current_price,
                "reason": sell_signal.reason,
                "order_id": ord_no,
                "fee": "",
                "tax": "",
                "cash_psbl": "",
                "balance_tot_asset": "",
                "balance_dnca": "",
                "balance_json": "",
                "pnl_cash_delta": "",
            })
            label = "가상매도" if settings.sim_mode else "매도"
            self.logger.info(
                f"[{label}] {symbol} price={quote.current_price} "
                f"qty={sell_signal.qty} reason={sell_signal.reason}"
                + (f" ord_no={ord_no}" if not settings.sim_mode else "")
            )
            if symbol in self.positions:
                self.positions.remove(symbol)

    def _ensure_cash_snapshot(self) -> bool:
        if self.cash_snapshot_done:
            return True
        if self.cash_snapshot_failed:
            return False

        hhmmss = self._now_kst().strftime("%H:%M:%S")
        if hhmmss < "09:00:05":
            return False
        if hhmmss > "09:05:00":
            self.cash_snapshot_failed = True
            self.logger.error("현금 스냅샷 실패(09:05 초과). 오늘은 매수 없이 감시만 진행합니다.")
            return False

        now_ts = time.time()
        if now_ts < self._next_cash_retry_ts:
            return False
        self._next_cash_retry_ts = now_ts + self.cash_retry_interval_sec

        try:
            cash = int(self.api.get_cash_balance() or 0)
        except Exception as exc:
            self.logger.error(f"현금 스냅샷 실패: {exc}")
            return False

        if cash <= 0:
            self.logger.error(f"현금 스냅샷 0원. 재시도 예정. cash={cash}")
            return False

        self.cash_snapshot_total = cash
        self.per_symbol_budget = int(cash * settings.allocation_per_symbol)
        self.cash_snapshot_done = True
        self.logger.info(
            f"현금 스냅샷 완료. at={hhmmss} KST "
            f"total={cash} per_symbol={self.per_symbol_budget} "
            f"max_positions={settings.max_positions}"
        )
        return True

    def _maybe_heartbeat(self, message: str) -> None:
        now_ts = time.time()
        if now_ts - self._last_heartbeat_ts < settings.heartbeat_sec:
            return
        self._last_heartbeat_ts = now_ts
        self.logger.info(message)
        self._hb_cycles = 0
        self._hb_signals = 0
        self._hb_buys = 0
        self._hb_quote_err = 0

    def _write_daily_result(self, ymd: str) -> None:
        if not settings.result_csv_on_shutdown:
            return
        try:
            from datetime import timedelta
            from core.naver_symbol_master import load_or_refresh_symbol_master
            from core.result_csv import build_daily_rows_from_kis_range
            from core.result_xlsx import append_result_xlsx_rows, paper_trades_to_execs

            if settings.sim_mode:
                execs = paper_trades_to_execs(self._paper_trades)
                kis_names: dict = {}
            else:
                from core.result_csv import kis_rows_to_execs, kis_rows_to_symbol_names
                lookback = max(1, min(90, settings.result_csv_kis_lookback_days))
                end_dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Seoul"))
                start_ymd = (end_dt - timedelta(days=lookback)).strftime("%Y%m%d")
                kis_rows = self.api.get_daily_order_executions(start_ymd, ymd)
                execs = kis_rows_to_execs(kis_rows)
                kis_names = kis_rows_to_symbol_names(kis_rows)

            daily_rows = build_daily_rows_from_kis_range(execs, ymd)
            names = load_or_refresh_symbol_master(
                settings.symbol_master_path,
                auto_refresh=settings.symbol_master_auto_refresh,
                max_age_days=settings.symbol_master_max_age_days,
                delay_sec=settings.naver_http_delay_sec,
            )
            append_result_xlsx_rows(
                settings.result_xlsx_path, daily_rows, names,
                kis_symbol_names=kis_names,
                strategy_mode=settings.strategy_mode,
                symbol_strategies=self.symbol_strategies,
            )
            self.logger.info(f"result.xlsx 갱신: {ymd} ({len(daily_rows)}건)")
        except Exception as exc:
            self.logger.error(f"result.xlsx 실패: {exc}")

    @staticmethod
    def _configure_console_utf8() -> None:
        try:
            import sys
            import os
            import platform

            os.environ.setdefault("PYTHONIOENCODING", "utf-8")
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            if platform.system().lower() == "windows":
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetConsoleOutputCP(65001)
                    ctypes.windll.kernel32.SetConsoleCP(65001)
                except Exception:
                    pass
        except Exception:
            return


if __name__ == "__main__":
    runner = MoaRunner()
    runner.run()
