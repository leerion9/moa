from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from config.settings import settings
from core.api_client import KISApiClient
from core.logger import TradeLogger
from core.order import OrderManager
from core.strategy import W52HighStrategy
from core.trading_day import should_run_bot_today_kst
from core.universe_cache import (
    CachedSymbol,
    UniverseCache,
    cache_path,
    load_cache,
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
        self.positions: List[str] = []         # 현재 보유 종목

        self.ordered_symbols_today: set[str] = set()
        self.buy_orders_today: int = 0

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
            f"strategy={settings.strategy_mode}"
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

        if hhmm >= settings.shutdown_hhmm:
            if self._did_result_csv_ymd != ymd:
                self._write_daily_result_csv(ymd)
                self._did_result_csv_ymd = ymd
            self._should_stop = True
            return

        now_ts = time.time()
        if now_ts - self._last_monitor_ts >= settings.poll_interval_sec:
            self._last_monitor_ts = now_ts
            self._monitor_watchlist()
            self._monitor_positions()

    def prepare_universe(self) -> None:
        """
        당일 감시 목록 준비.
        캐시가 있으면 재사용, 없으면 유니버스 빌더(Phase 2)에서 생성.
        """
        self.logger.info("유니버스 준비 중...")

        cache_date = today_kst_yyyymmdd(self._now_kst())
        ucache_path = cache_path(Path("data"), cache_date)
        cached = load_cache(ucache_path, strategy_mode=settings.strategy_mode)

        if cached is not None and cached.date_kst == cache_date:
            self.watchlist = list(cached.symbols.keys())
            for symbol, feat in cached.symbols.items():
                self.strategy.register(
                    symbol=symbol,
                    w52_high=feat.w52_high,
                    vol_ma20=feat.vol_ma20,
                )
            self.logger.info(
                f"유니버스 캐시 로드: {ucache_path.name} ({len(self.watchlist)}종목)"
            )
        else:
            # Phase 2에서 실제 유니버스 빌더 구현
            self.logger.info(
                "유니버스 캐시 없음. 유니버스 빌더 미구현(Phase 2). 감시 목록 비어 있습니다."
            )
            self.watchlist = []

        self.logger.info(
            f"감시 준비 완료. 종목={len(self.watchlist)}개 "
            f"전략={settings.strategy_mode} "
            f"({settings.monitor_start_hhmm}~{settings.monitor_end_hhmm} KST)"
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

        if not self._ensure_cash_snapshot():
            return

        can_buy = self.buy_orders_today < settings.max_positions
        budget = int(self.per_symbol_budget or 0)
        if budget <= 0:
            self.logger.error("per_symbol_budget 이상. 감시만 진행합니다.")
            return

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
                "action": "SKIP_FULL_CAP" if not can_buy else "BUY_ATTEMPT",
                "note": "",
            })

            if not can_buy:
                continue

            if signal.entry_price > budget:
                self.logger.log_signal({
                    "symbol": symbol,
                    "breakout_price": signal.entry_price,
                    "reason": signal.reason,
                    "action": "SKIP_HIGH_PRICE",
                    "note": "1주가 예산 초과",
                })
                continue

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
            self.logger.info(
                f"[매수] {symbol} price={signal.entry_price} qty={qty} ord_no={ord_no}"
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
            self.logger.info(
                f"[매도] {symbol} price={quote.current_price} "
                f"qty={sell_signal.qty} reason={sell_signal.reason} ord_no={ord_no}"
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

    def _write_daily_result_csv(self, ymd: str) -> None:
        if not settings.result_csv_on_shutdown:
            return
        try:
            from datetime import timedelta
            from core.naver_symbol_master import load_or_refresh_symbol_master
            from core.result_csv import (
                append_result_rows,
                build_daily_rows_from_kis_range,
                kis_rows_to_execs,
                kis_rows_to_symbol_names,
            )

            lookback = max(1, min(90, settings.result_csv_kis_lookback_days))
            end_dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("Asia/Seoul"))
            start_ymd = (end_dt - timedelta(days=lookback)).strftime("%Y%m%d")
            kis_rows = self.api.get_daily_order_executions(start_ymd, ymd)
            execs = kis_rows_to_execs(kis_rows)
            daily_rows = build_daily_rows_from_kis_range(execs, ymd)
            names = load_or_refresh_symbol_master(
                settings.symbol_master_path,
                auto_refresh=settings.symbol_master_auto_refresh,
                max_age_days=settings.symbol_master_max_age_days,
                delay_sec=settings.naver_http_delay_sec,
            )
            kis_names = kis_rows_to_symbol_names(kis_rows)
            append_result_rows(
                settings.result_csv_path, daily_rows, names, kis_symbol_names=kis_names
            )
            self.logger.info(f"result.csv 갱신: {ymd} ({len(daily_rows)}건)")
        except Exception as exc:
            self.logger.error(f"result.csv 실패: {exc}")

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
