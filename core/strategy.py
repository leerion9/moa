from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class SymbolState:
    """종목별 감시 상태 (52주 신고가 돌파 매매)."""
    w52_high: int       # 52주 신고가 가격
    vol_ma20: int       # 20일 평균 거래량

    bought: bool = False        # 오늘 매수 주문 완료 여부

    # 포지션 추적 (매수 이후)
    buy_price: int = 0          # 매수 체결가
    qty: int = 0                # 보유 수량
    peak_price: int = 0         # 매수 후 최고가 (트레일링 스탑용)


@dataclass
class EntrySignal:
    symbol: str
    entry_price: int            # 52주 신고가 = 진입 호가
    reason: str = "w52_high_breakout"


@dataclass
class SellSignal:
    symbol: str
    qty: int
    reason: str                 # "trailing_stop"


@dataclass
class W52HighStrategy:
    """
    52주 신고가 돌파 매매 전략.

    매수: 현재가 >= 52주 신고가 AND 당일 거래량 >= 20일 평균 거래량
    매도: 최고가 대비 trailing_stop_pct 이상 하락 시 전량 시장가
    """
    trailing_stop_pct: float
    symbol_state: Dict[str, SymbolState] = field(default_factory=dict)

    def register(self, symbol: str, w52_high: int, vol_ma20: int) -> None:
        """감시 목록에 종목 등록."""
        self.symbol_state[symbol] = SymbolState(
            w52_high=w52_high,
            vol_ma20=vol_ma20,
        )

    def register_position(self, symbol: str, buy_price: int, qty: int) -> None:
        """매수 체결 후 포지션 등록 (트레일링 스탑 추적 시작)."""
        state = self.symbol_state.get(symbol)
        if state is None:
            return
        state.buy_price = buy_price
        state.qty = qty
        state.peak_price = buy_price

    def on_quote(self, symbol: str, current_price: int, current_volume: int) -> Optional[EntrySignal]:
        """
        장중 시세 수신 시 매수 신호 체크.
        이미 매수된 종목은 None 반환.

        거래량 미충족 시에도 skip 처리하지 않고 다음 tick에 재확인한다.
        (오전 거래량 부족 → 오후 충족 케이스 대응)
        """
        state = self.symbol_state.get(symbol)
        if state is None or state.bought:
            return None

        if current_price < state.w52_high:
            return None

        # 거래량 조건: 당일 누적 거래량 >= 20일 평균. 미충족 시 이번 tick만 패스.
        if current_volume < state.vol_ma20:
            return None

        state.bought = True
        return EntrySignal(symbol=symbol, entry_price=state.w52_high)

    def on_position_quote(self, symbol: str, current_price: int) -> Optional[SellSignal]:
        """
        보유 포지션 시세 수신 시 트레일링 스탑 체크.
        trailing_stop_pct 이상 고점 대비 하락 시 전량 매도 신호 반환.
        """
        state = self.symbol_state.get(symbol)
        if state is None or state.qty <= 0:
            return None

        if current_price > state.peak_price:
            state.peak_price = current_price

        if state.peak_price > 0:
            drop = (state.peak_price - current_price) / state.peak_price
            if drop >= self.trailing_stop_pct:
                qty = state.qty
                state.qty = 0
                return SellSignal(symbol=symbol, qty=qty, reason="trailing_stop")

        return None

    def held_symbols(self) -> list[str]:
        """현재 포지션 보유 중인 종목 목록."""
        return [s for s, st in self.symbol_state.items() if st.qty > 0]

    def watchlist_symbols(self) -> list[str]:
        """아직 매수 안 된 감시 중 종목 목록."""
        return [s for s, st in self.symbol_state.items() if not st.bought]
