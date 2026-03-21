"""
execution/mt5.py — MetaTrader 5 Python Bridge Adapter.
Requires: MetaTrader 5 terminal installed on Windows + MetaTrader5 Python package.
This is a stub — fully stubbed for cross-platform compatibility, operational on Windows+MT5.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from execution.broker_interface import (
    AccountInfo, BrokerInterface, CloseResult, OrderResult, Position
)
from utils.logger import get_logger

logger = get_logger("mt5_broker")

# MT5 trade type constants
ORDER_TYPE_BUY = 0
ORDER_TYPE_SELL = 1
TRADE_ACTION_DEAL = 1
ORDER_FILLING_IOC = 1


class MT5Broker(BrokerInterface):
    """
    MetaTrader 5 Bridge Adapter.
    Requires MetaTrader5 Python package (Windows only).
    Install: pip install MetaTrader5
    """
    broker_name = "mt5"

    def __init__(self, login: int, password: str, server: str):
        self._login = login
        self._password = password
        self._server = server
        self._mt5 = None
        self._connected = False
        self._init()

    def _init(self) -> None:
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
            if not mt5.initialize(login=self._login, password=self._password, server=self._server):
                err = mt5.last_error()
                logger.error(f"MT5 init failed: {err}")
                self._connected = False
            else:
                self._connected = True
                logger.info(f"MT5 connected: {self._server} account={self._login}")
        except ImportError:
            logger.warning("MetaTrader5 not installed. MT5 adapter disabled.")
            self._connected = False

    def is_connected(self) -> bool:
        if not self._mt5 or not self._connected:
            return False
        try:
            return self._mt5.terminal_info() is not None
        except Exception:
            return False

    def get_account_info(self) -> AccountInfo:
        if not self._connected:
            raise RuntimeError("MT5 not connected")
        info = self._mt5.account_info()
        return AccountInfo(
            account_id=str(self._login),
            balance=info.balance,
            equity=info.equity,
            unrealized_pnl=info.equity - info.balance,
            margin_used=info.margin,
            margin_available=info.margin_free,
            currency=info.currency,
            broker="mt5",
        )

    def get_price(self, symbol: str) -> tuple[float, float]:
        """Symbol format: EURUSD (no underscore for MT5)"""
        mt5_symbol = symbol.replace("_", "")
        tick = self._mt5.symbol_info_tick(mt5_symbol)
        if tick is None:
            raise RuntimeError(f"MT5: no tick data for {mt5_symbol}")
        return tick.bid, tick.ask

    def place_order(
        self,
        symbol: str,
        side: str,
        units: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        mt5_symbol = symbol.replace("_", "")
        order_type = ORDER_TYPE_BUY if side.upper() == "BUY" else ORDER_TYPE_SELL
        tick = self._mt5.symbol_info_tick(mt5_symbol)
        price = tick.ask if side.upper() == "BUY" else tick.bid

        # Units in MT5 are in lots (1 lot = 100,000 units)
        lots = max(0.01, round(units / 100_000, 2))

        request = {
            "action": TRADE_ACTION_DEAL,
            "symbol": mt5_symbol,
            "volume": lots,
            "type": order_type,
            "price": price,
            "deviation": 10,          # Max slippage in points
            "magic": 20260321,        # Magic number for identification
            "comment": "AI_TRADER",
            "type_filling": ORDER_FILLING_IOC,
        }
        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        result = self._mt5.order_send(request)
        if result and result.retcode == 10009:  # TRADE_RETCODE_DONE
            return OrderResult(
                order_id=str(result.order),
                trade_id=str(result.deal),
                symbol=symbol,
                side=side,
                units=lots * 100_000,
                fill_price=result.price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                timestamp=datetime.now(timezone.utc),
                status="FILLED",
            )
        else:
            err = result.comment if result else "MT5 order failed"
            return OrderResult(
                order_id="", trade_id="", symbol=symbol, side=side,
                units=units, fill_price=0, stop_loss=stop_loss, take_profit=take_profit,
                timestamp=datetime.now(timezone.utc), status="REJECTED", error=err,
            )

    def close_position(self, trade_id: str) -> CloseResult:
        positions = self._mt5.positions_get(ticket=int(trade_id))
        if not positions:
            return CloseResult(
                trade_id=trade_id, realized_pnl=0, close_price=0,
                timestamp=datetime.now(timezone.utc), status="ERROR",
                error="Position not found"
            )
        pos = positions[0]
        close_type = ORDER_TYPE_SELL if pos.type == ORDER_TYPE_BUY else ORDER_TYPE_BUY
        tick = self._mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if close_type == ORDER_TYPE_SELL else tick.ask

        request = {
            "action": TRADE_ACTION_DEAL,
            "position": pos.ticket,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "price": price,
            "deviation": 10,
            "magic": 20260321,
            "comment": "AI_CLOSE",
            "type_filling": ORDER_FILLING_IOC,
        }
        result = self._mt5.order_send(request)
        pnl = pos.profit if result and result.retcode == 10009 else 0.0
        return CloseResult(
            trade_id=trade_id,
            realized_pnl=pnl,
            close_price=price,
            timestamp=datetime.now(timezone.utc),
            status="CLOSED" if (result and result.retcode == 10009) else "ERROR",
        )

    def get_open_positions(self) -> List[Position]:
        raw = self._mt5.positions_get() or []
        result = []
        for pos in raw:
            result.append(Position(
                trade_id=str(pos.ticket),
                symbol=pos.symbol,
                side="BUY" if pos.type == ORDER_TYPE_BUY else "SELL",
                units=pos.volume * 100_000,
                entry_price=pos.price_open,
                current_price=pos.price_current,
                unrealized_pnl=pos.profit,
                stop_loss=pos.sl if pos.sl > 0 else None,
                take_profit=pos.tp if pos.tp > 0 else None,
                open_time=datetime.now(timezone.utc),
            ))
        return result

    def shutdown(self) -> None:
        if self._mt5 and self._connected:
            self._mt5.shutdown()
            logger.info("MT5 connection closed")
