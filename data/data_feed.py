"""
data/data_feed.py — Market data feed with Yahoo Finance primary, 
broker secondary, and internal fallback simulator.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import pandas as pd
import yfinance as yf

from config import get_config
from utils.logger import get_logger

logger = get_logger("data_feed")


@dataclass
class OHLCV:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass  
class MarketSnapshot:
    """Single point-in-time market state, normalized across all data sources."""
    symbol: str
    bid: float
    ask: float
    mid: float
    timestamp: datetime
    spread_pips: float
    ohlcv_1m: List[OHLCV] = field(default_factory=list)
    ohlcv_5m: List[OHLCV] = field(default_factory=list)
    ohlcv_15m: List[OHLCV] = field(default_factory=list)
    ohlcv_1h: List[OHLCV] = field(default_factory=list)
    ohlcv_1d: List[OHLCV] = field(default_factory=list)
    source: str = "unknown"

    @property
    def price(self) -> float:
        return self.mid

    def latest_close(self, timeframe: str = "1h") -> Optional[float]:
        mapping = {
            "1m": self.ohlcv_1m,
            "5m": self.ohlcv_5m,
            "15m": self.ohlcv_15m,
            "1h": self.ohlcv_1h,
            "1d": self.ohlcv_1d,
        }
        bars = mapping.get(timeframe, self.ohlcv_1h)
        return bars[-1].close if bars else self.mid


class _YahooFinanceFeed:
    """Primary data source: Yahoo Finance via yfinance."""

    TIMEFRAME_MAP = {
        "1m": ("1m", "1d"),
        "5m": ("5m", "5d"),
        "15m": ("15m", "60d"),
        "1h": ("1h", "90d"),
        "1d": ("1d", "2y"),
    }

    def _fetch_bars(self, yahoo_symbol: str, interval: str, period: str) -> List[OHLCV]:
        try:
            ticker = yf.Ticker(yahoo_symbol)
            df = ticker.history(interval=interval, period=period, auto_adjust=True)
            if df.empty:
                return []
            bars = []
            for ts, row in df.iterrows():
                bars.append(OHLCV(
                    timestamp=ts.to_pydatetime().replace(tzinfo=timezone.utc),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0)),
                ))
            return bars
        except Exception as e:
            logger.warning(f"Yahoo Finance fetch [{interval}/{period}] failed: {e}")
            return []

    def get_snapshot(self, yahoo_symbol: str, base_symbol: str) -> Optional[MarketSnapshot]:
        try:
            ticker = yf.Ticker(yahoo_symbol)
            info = ticker.fast_info

            # Try to get current price
            price = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            if not price:
                hist = ticker.history(period="1d", interval="1m")
                if hist.empty:
                    return None
                price = float(hist["Close"].iloc[-1])

            spread_estimate = price * 0.00015  # ~1.5 pip spread estimate
            bid = price - spread_estimate / 2
            ask = price + spread_estimate / 2

            # Fetch OHLCV for multiple timeframes
            ohlcv_1m = self._fetch_bars(yahoo_symbol, "1m", "1d")
            ohlcv_5m = self._fetch_bars(yahoo_symbol, "5m", "5d")
            ohlcv_15m = self._fetch_bars(yahoo_symbol, "15m", "60d")
            ohlcv_1h = self._fetch_bars(yahoo_symbol, "1h", "90d")
            ohlcv_1d = self._fetch_bars(yahoo_symbol, "1d", "2y")

            return MarketSnapshot(
                symbol=base_symbol,
                bid=round(bid, 5),
                ask=round(ask, 5),
                mid=round(price, 5),
                timestamp=datetime.now(timezone.utc),
                spread_pips=round(spread_estimate / 0.0001, 1),
                ohlcv_1m=ohlcv_1m,
                ohlcv_5m=ohlcv_5m,
                ohlcv_15m=ohlcv_15m,
                ohlcv_1h=ohlcv_1h,
                ohlcv_1d=ohlcv_1d,
                source="yahoo_finance",
            )
        except Exception as e:
            logger.error(f"Yahoo Finance snapshot failed: {e}")
            return None


class _SimulatorFeed:
    """
    Internal random-walk price simulator for development/testing.
    Uses a geometric Brownian motion model for EUR/USD.
    """

    def __init__(self, initial_price: float = 1.0850):
        self.price = initial_price
        self.volatility = 0.0005  # Daily vol ~0.5%
        self._bars_1h: List[OHLCV] = self._seed_bars(100)

    def _random_step(self) -> float:
        """GBM step."""
        drift = 0.0
        sigma = self.volatility / math.sqrt(252 * 24)
        shock = random.gauss(0, 1)
        self.price *= math.exp(drift + sigma * shock)
        return round(self.price, 5)

    def _seed_bars(self, n: int) -> List[OHLCV]:
        bars = []
        price = self.price
        base_ts = datetime.now(timezone.utc)
        for i in range(n):
            o = price
            price += random.gauss(0, 0.0003)
            c = price
            h = max(o, c) + abs(random.gauss(0, 0.0001))
            l = min(o, c) - abs(random.gauss(0, 0.0001))
            ts = base_ts.replace(hour=(i % 24))
            bars.append(OHLCV(ts, round(o, 5), round(h, 5), round(l, 5), round(c, 5), 0))
        return bars

    def get_snapshot(self, yahoo_symbol: str, base_symbol: str) -> MarketSnapshot:
        price = self._random_step()
        spread = 0.00012
        # Append new bar
        latest_bar = OHLCV(datetime.now(timezone.utc), price, price + 0.0002, price - 0.0002, price, 0)
        self._bars_1h.append(latest_bar)
        if len(self._bars_1h) > 500:
            self._bars_1h = self._bars_1h[-500:]

        return MarketSnapshot(
            symbol=base_symbol,
            bid=round(price - spread / 2, 5),
            ask=round(price + spread / 2, 5),
            mid=price,
            timestamp=datetime.now(timezone.utc),
            spread_pips=round(spread / 0.0001, 1),
            ohlcv_1h=list(self._bars_1h),
            ohlcv_1d=list(self._bars_1h[::24]),
            source="simulator",
        )


class DataFeed:
    """
    Unified data feed with automatic fallback:
    Yahoo Finance → Broker → Simulator
    """

    def __init__(self):
        self.cfg = get_config()
        self._yahoo = _YahooFinanceFeed()
        self._simulator = _SimulatorFeed()
        self._last_snapshot: Optional[MarketSnapshot] = None
        self._cache_ttl = 30  # seconds
        self._cache_ts: float = 0.0

    def get_snapshot(self, force_refresh: bool = False) -> MarketSnapshot:
        now = time.monotonic()
        if not force_refresh and self._last_snapshot and (now - self._cache_ts < self._cache_ttl):
            return self._last_snapshot

        yahoo_symbol = self.cfg.yahoo_symbol
        base_symbol = self.cfg.trading_symbol

        # Primary: Yahoo Finance
        snapshot = self._yahoo.get_snapshot(yahoo_symbol, base_symbol)

        # Fallback: Simulator
        if snapshot is None:
            logger.warning("Yahoo Finance unavailable — using internal simulator")
            snapshot = self._simulator.get_snapshot(yahoo_symbol, base_symbol)

        self._last_snapshot = snapshot
        self._cache_ts = now
        logger.debug(f"Market snapshot: {snapshot.symbol} mid={snapshot.mid} source={snapshot.source}")
        return snapshot

    def get_ohlcv_dataframe(self, timeframe: str = "1h") -> pd.DataFrame:
        """Return OHLCV bars as a pandas DataFrame for indicator calculation."""
        snapshot = self.get_snapshot()
        tf_map = {
            "1m": snapshot.ohlcv_1m,
            "5m": snapshot.ohlcv_5m,
            "15m": snapshot.ohlcv_15m,
            "1h": snapshot.ohlcv_1h,
            "1d": snapshot.ohlcv_1d,
        }
        bars = tf_map.get(timeframe, snapshot.ohlcv_1h)
        if not bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        return pd.DataFrame([{
            "timestamp": b.timestamp,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
            "volume": b.volume,
        } for b in bars]).set_index("timestamp")
