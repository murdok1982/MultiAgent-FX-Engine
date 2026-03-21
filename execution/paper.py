"""
execution/paper.py — Paper Trading Simulator Broker.
Simulates fills at mid-price + configurable spread.
Identical interface to real brokers — safe for development and testing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from execution.broker_interface import (
    AccountInfo, BrokerInterface, CloseResult, OrderResult, Position
)
from utils.logger import get_logger

logger = get_logger("paper_broker")


@dataclass
class _PaperTrade:
    trade_id: str
    symbol: str
    side: str
    units: float
    entry_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    open_time: datetime
    current_price: float = 0.0


class PaperBroker(BrokerInterface):
    """
    Paper trading simulator.
    All trades are virtual — no real money involved.
    Tracks P&L, simulates fills at mid-price with spread.
    """
    broker_name = "paper"

    SPREAD = 0.00012  # ~1.2 pip spread simulation

    def __init__(self, initial_balance: float = 10_000.0):
        self._balance = initial_balance
        self._initial_balance = initial_balance
        self._open_trades: Dict[str, _PaperTrade] = {}
        self._realized_pnl = 0.0
        self._trade_count = 0
        logger.info(f"Paper broker initialized. Balance: ${initial_balance:,.2f}")

    def is_connected(self) -> bool:
        return True

    def get_account_info(self) -> AccountInfo:
        unrealized = sum(
            self._calc_unrealized_pnl(t) for t in self._open_trades.values()
        )
        return AccountInfo(
            account_id="PAPER_ACCOUNT",
            balance=self._balance,
            equity=self._balance + unrealized,
            unrealized_pnl=unrealized,
            margin_used=sum(t.entry_price * t.units * 0.02 for t in self._open_trades.values()),
            margin_available=self._balance,
            currency="USD",
            broker="paper",
        )

    def get_price(self, symbol: str) -> tuple[float, float]:
        """Simulate price from last known mid + spread."""
        # For paper mode, we rely on external data feed for mid price
        # Return a placeholder — real price injection happens in main loop
        mid = self._last_known_price.get(symbol, 1.0850)
        return mid - self.SPREAD / 2, mid + self.SPREAD / 2

    _last_known_price: Dict[str, float] = {}

    def update_price(self, symbol: str, mid: float) -> None:
        """Inject current market price for SL/TP monitoring."""
        self._last_known_price[symbol] = mid
        # Check SL/TP for all open positions
        to_close = []
        for trade_id, trade in self._open_trades.items():
            if trade.symbol != symbol:
                continue
            trade.current_price = mid
            if trade.stop_loss and trade.side == "BUY" and mid <= trade.stop_loss:
                to_close.append((trade_id, "SL hit"))
            elif trade.stop_loss and trade.side == "SELL" and mid >= trade.stop_loss:
                to_close.append((trade_id, "SL hit"))
            elif trade.take_profit and trade.side == "BUY" and mid >= trade.take_profit:
                to_close.append((trade_id, "TP hit"))
            elif trade.take_profit and trade.side == "SELL" and mid <= trade.take_profit:
                to_close.append((trade_id, "TP hit"))

        for trade_id, reason in to_close:
            result = self.close_position(trade_id)
            logger.info(f"Auto-close [{reason}] trade {trade_id}: PnL={result.realized_pnl:+.2f}")

    def place_order(
        self,
        symbol: str,
        side: str,
        units: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        if units <= 0:
            return OrderResult(
                order_id="", trade_id="", symbol=symbol, side=side,
                units=0, fill_price=0, stop_loss=stop_loss, take_profit=take_profit,
                timestamp=datetime.now(timezone.utc), status="REJECTED",
                error="Units must be positive"
            )

        mid = self._last_known_price.get(symbol, 1.0850)
        # Simulate slippage: buy at ask, sell at bid
        fill_price = (mid + self.SPREAD / 2) if side.upper() == "BUY" else (mid - self.SPREAD / 2)

        trade_id = f"PAPER-{uuid.uuid4().hex[:8].upper()}"
        self._trade_count += 1

        trade = _PaperTrade(
            trade_id=trade_id,
            symbol=symbol,
            side=side.upper(),
            units=units,
            entry_price=fill_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            open_time=datetime.now(timezone.utc),
            current_price=mid,
        )
        self._open_trades[trade_id] = trade

        logger.info(
            f"[PAPER] OPEN {side} {units:.0f} {symbol} @ {fill_price:.5f} "
            f"SL={stop_loss} TP={take_profit} | ID={trade_id}"
        )

        return OrderResult(
            order_id=f"ORD-{self._trade_count}",
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            units=units,
            fill_price=round(fill_price, 5),
            stop_loss=stop_loss,
            take_profit=take_profit,
            timestamp=datetime.now(timezone.utc),
            status="FILLED",
        )

    def close_position(self, trade_id: str) -> CloseResult:
        trade = self._open_trades.pop(trade_id, None)
        if trade is None:
            return CloseResult(
                trade_id=trade_id, realized_pnl=0, close_price=0,
                timestamp=datetime.now(timezone.utc), status="ERROR",
                error="Trade not found"
            )

        mid = self._last_known_price.get(trade.symbol, trade.entry_price)
        close_price = (mid - self.SPREAD / 2) if trade.side == "BUY" else (mid + self.SPREAD / 2)

        pnl = self._calc_pnl_at_price(trade, close_price)
        self._balance += pnl
        self._realized_pnl += pnl

        logger.info(
            f"[PAPER] CLOSE {trade.side} {trade.units:.0f} {trade.symbol} "
            f"@ {close_price:.5f} | PnL={pnl:+.2f} | Balance={self._balance:.2f}"
        )

        return CloseResult(
            trade_id=trade_id,
            realized_pnl=round(pnl, 2),
            close_price=round(close_price, 5),
            timestamp=datetime.now(timezone.utc),
            status="CLOSED",
        )

    def get_open_positions(self) -> List[Position]:
        return [
            Position(
                trade_id=t.trade_id,
                symbol=t.symbol,
                side=t.side,
                units=t.units,
                entry_price=t.entry_price,
                current_price=t.current_price or t.entry_price,
                unrealized_pnl=self._calc_unrealized_pnl(t),
                stop_loss=t.stop_loss,
                take_profit=t.take_profit,
                open_time=t.open_time,
            )
            for t in self._open_trades.values()
        ]

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _calc_pnl_at_price(self, trade: _PaperTrade, close_price: float) -> float:
        if trade.side == "BUY":
            return (close_price - trade.entry_price) * trade.units
        else:
            return (trade.entry_price - close_price) * trade.units

    def _calc_unrealized_pnl(self, trade: _PaperTrade) -> float:
        mid = self._last_known_price.get(trade.symbol, trade.entry_price)
        return self._calc_pnl_at_price(trade, mid)

    @property
    def total_return_pct(self) -> float:
        return ((self._balance - self._initial_balance) / self._initial_balance) * 100
