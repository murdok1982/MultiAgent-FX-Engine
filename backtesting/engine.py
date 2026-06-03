"""
Backtesting engine that replays historical OHLCV data through the agent pipeline.
Uses TechnicalAgent (pure Python) + rule-based orchestrator (no LLM).
"""
from __future__ import annotations
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import numpy as np


@dataclass
class BacktestConfig:
    symbol: str = "EURUSD=X"
    start_date: str = "2024-01-01"
    end_date: str = "2024-12-31"
    initial_balance: float = 10_000.0
    risk_per_trade_pct: float = 1.0
    max_daily_drawdown_pct: float = 2.0
    sl_multiplier: float = 1.5
    tp_multiplier: float = 2.5
    min_strength_threshold: float = 0.55
    timeframe: str = "1h"
    spread_pips: float = 1.2  # 1.2 pips simulated spread


@dataclass
class BacktestTrade:
    trade_id: str
    symbol: str
    side: str
    entry_time: datetime
    entry_price: float
    stop_loss: float
    take_profit: float
    units: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    exit_reason: Optional[str] = None  # "SL" | "TP" | "END_OF_DATA"
    strength: float = 0.0
    regime: str = "UNKNOWN"


class BacktestEngine:
    def __init__(self, config: BacktestConfig):
        self.config = config
        self._balance = config.initial_balance
        self._daily_opening_balance = config.initial_balance
        self._equity_curve: List[float] = [config.initial_balance]
        self._timestamps: List[datetime] = []
        self._trades: List[BacktestTrade] = []
        self._current_trade: Optional[BacktestTrade] = None
        self._daily_losses = 0.0
        self._kill_switch = False

    async def run(self) -> "BacktestResult":
        """Main backtest loop."""
        df = self._download_data()
        if df is None or df.empty:
            raise ValueError(f"No data downloaded for {self.config.symbol}")

        # Build synthetic OHLCV list
        from data.data_feed import OHLCV
        all_candles: List[OHLCV] = []
        for idx, row in df.iterrows():
            all_candles.append(OHLCV(
                timestamp=row.name.to_pydatetime() if hasattr(row.name, 'to_pydatetime') else datetime.now(timezone.utc),
                open=float(row['Open']),
                high=float(row['High']),
                low=float(row['Low']),
                close=float(row['Close']),
                volume=float(row.get('Volume', 0)),
            ))

        min_candles = 210  # Need enough for EMA200
        if len(all_candles) < min_candles:
            raise ValueError(f"Insufficient data: {len(all_candles)} candles (need {min_candles})")

        # Walk forward
        for i in range(min_candles, len(all_candles)):
            candle = all_candles[i]
            current_time = candle.timestamp
            mid_price = candle.close
            ask_price = mid_price + (self.config.spread_pips * 0.0001 / 2)
            bid_price = mid_price - (self.config.spread_pips * 0.0001 / 2)

            # Daily reset
            if self._timestamps and current_time.date() != self._timestamps[-1].date():
                self._daily_opening_balance = self._balance
                self._daily_losses = 0.0
                self._kill_switch = False

            self._timestamps.append(current_time)

            # Check kill switch
            if self._kill_switch:
                self._equity_curve.append(self._balance)
                continue

            # Monitor open position
            if self._current_trade:
                closed = self._check_position(self._current_trade, bid_price, ask_price)
                if closed:
                    self._current_trade = None

            # Run analysis (only enter new trade if no open position)
            if self._current_trade is None:
                window = all_candles[max(0, i - 200): i + 1]
                tech = await self._run_technical(window)

                if tech and tech.entry_signal:
                    risk_ok = self._check_daily_risk()
                    if risk_ok:
                        entry_price = ask_price if tech.signal_direction == "BUY" else bid_price
                        units = self._calc_units(entry_price, tech.suggested_sl)
                        if units >= 100:
                            trade = BacktestTrade(
                                trade_id=str(uuid.uuid4())[:8],
                                symbol=self.config.symbol,
                                side=tech.signal_direction,
                                entry_time=current_time,
                                entry_price=entry_price,
                                stop_loss=tech.suggested_sl,
                                take_profit=tech.suggested_tp,
                                units=units,
                                strength=tech.strength,
                                regime=getattr(tech, 'regime', 'UNKNOWN'),
                            )
                            self._trades.append(trade)
                            self._current_trade = trade

            self._equity_curve.append(self._balance)

        # Close open position at end of data
        if self._current_trade:
            last_price = all_candles[-1].close
            self._close_position(self._current_trade, last_price, "END_OF_DATA", all_candles[-1].timestamp)
            self._current_trade = None

        from backtesting.metrics import BacktestResult
        return BacktestResult(
            config=self.config,
            trades=self._trades,
            equity_curve=self._equity_curve,
            timestamps=self._timestamps,
        )

    def _download_data(self):
        """Download OHLCV from yfinance."""
        try:
            import yfinance as yf
            df = yf.download(
                self.config.symbol,
                start=self.config.start_date,
                end=self.config.end_date,
                interval="1h",
                auto_adjust=True,
                progress=False,
            )
            if hasattr(df.columns, 'droplevel'):
                try:
                    df.columns = df.columns.droplevel(1)
                except Exception:
                    pass
            return df
        except Exception as e:
            print(f"[Backtest] Download failed: {e}")
            return None

    async def _run_technical(self, candles):
        """Run technical analysis on a window of candles."""
        try:
            from agents.technical import TechnicalAgent
            from data.data_feed import MarketSnapshot
            from datetime import datetime, timezone

            agent = TechnicalAgent()
            snapshot = MarketSnapshot(
                symbol=self.config.symbol,
                bid=candles[-1].close - 0.00006,
                ask=candles[-1].close + 0.00006,
                mid=candles[-1].close,
                timestamp=candles[-1].timestamp if hasattr(candles[-1], 'timestamp') else datetime.now(timezone.utc),
                spread_pips=1.2,
                ohlcv_1m=[],
                ohlcv_5m=[],
                ohlcv_15m=candles[-20:],
                ohlcv_1h=candles,
                ohlcv_1d=[],
                source="backtest",
            )
            result = await agent.safe_analyze(snapshot=snapshot)
            return result
        except Exception:
            return None

    def _check_position(self, trade: BacktestTrade, bid: float, ask: float) -> bool:
        """Check if SL/TP is hit. Returns True if position was closed."""
        if trade.side == "BUY":
            if bid <= trade.stop_loss:
                self._close_position(trade, trade.stop_loss, "SL", datetime.now(timezone.utc))
                return True
            if bid >= trade.take_profit:
                self._close_position(trade, trade.take_profit, "TP", datetime.now(timezone.utc))
                return True
        else:  # SELL
            if ask >= trade.stop_loss:
                self._close_position(trade, trade.stop_loss, "SL", datetime.now(timezone.utc))
                return True
            if ask <= trade.take_profit:
                self._close_position(trade, trade.take_profit, "TP", datetime.now(timezone.utc))
                return True
        return False

    def _close_position(self, trade: BacktestTrade, close_price: float, reason: str, ts: datetime) -> None:
        """Close a position and update balance."""
        if trade.side == "BUY":
            pnl = (close_price - trade.entry_price) * trade.units
        else:
            pnl = (trade.entry_price - close_price) * trade.units

        trade.exit_price = close_price
        trade.exit_time = ts
        trade.pnl = round(pnl, 2)
        trade.exit_reason = reason
        self._balance += pnl

        if pnl < 0:
            self._daily_losses += abs(pnl)
            dd_pct = self._daily_losses / self._daily_opening_balance * 100
            if dd_pct >= self.config.max_daily_drawdown_pct:
                self._kill_switch = True

    def _check_daily_risk(self) -> bool:
        """True if daily risk limits are not breached."""
        if self._kill_switch:
            return False
        dd_pct = self._daily_losses / max(self._daily_opening_balance, 1) * 100
        return dd_pct < self.config.max_daily_drawdown_pct

    def _calc_units(self, entry: float, stop_loss: float) -> float:
        """Fixed-risk position sizing."""
        stop_distance = abs(entry - stop_loss)
        if stop_distance < 0.00001:
            return 0.0
        risk_amount = self._balance * (self.config.risk_per_trade_pct / 100)
        units = risk_amount / stop_distance
        max_units = (self._balance * 0.10) / entry * 100_000
        return round(min(units, max_units), 0)
