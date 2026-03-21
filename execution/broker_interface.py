"""
execution/broker_interface.py — Abstract Broker Interface.
All broker adapters (OANDA, MT5, IB, Paper) must implement this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional


@dataclass
class AccountInfo:
    account_id: str
    balance: float
    equity: float
    unrealized_pnl: float
    margin_used: float
    margin_available: float
    currency: str
    broker: str


@dataclass
class OrderResult:
    order_id: str
    trade_id: str
    symbol: str
    side: str
    units: float
    fill_price: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    timestamp: datetime
    status: str  # FILLED | REJECTED | PENDING
    error: Optional[str] = None


@dataclass
class Position:
    trade_id: str
    symbol: str
    side: str
    units: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    open_time: datetime


@dataclass
class CloseResult:
    trade_id: str
    realized_pnl: float
    close_price: float
    timestamp: datetime
    status: str
    error: Optional[str] = None


class BrokerInterface(ABC):
    """
    Abstract broker interface. Implement this for any broker.
    All methods must be synchronous for simple integration.
    """

    broker_name: str = "abstract"

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        """Return current account state (balance, margin, equity)."""
        ...

    @abstractmethod
    def get_price(self, symbol: str) -> tuple[float, float]:
        """Return (bid, ask) for given symbol."""
        ...

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        units: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> OrderResult:
        """Place a market order. Returns OrderResult."""
        ...

    @abstractmethod
    def close_position(self, trade_id: str) -> CloseResult:
        """Close an open position by trade_id."""
        ...

    @abstractmethod
    def get_open_positions(self) -> List[Position]:
        """Return all currently open positions."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Check broker connectivity."""
        ...
