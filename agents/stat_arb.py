"""
agents/stat_arb.py — Mean-Reversion Statistical Arbitrage Agent (intraday)
==========================================================================

Classical mean-reversion logic on the intraday timeframe:

  • Z-score of close vs N-bar rolling mean
  • Bollinger %B (alternative bounded form)
  • Hurst exponent estimate — only fade when H < 0.5 (mean-reverting regime)
  • Half-life of reversion (Ornstein-Uhlenbeck OLS)
  • Signal: |z| ≥ entry_z fades the move; exit at z=0 or stop at exit_z

Output integrates with the orchestrator: BIAS BUY/SELL when the agent
believes the move is overextended and likely to revert.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from data.data_feed import DataFeed, MarketSnapshot


class StatArbSignal(BaseModel):
    bias: str = Field(default="NEUTRAL", description="BUY | SELL | NEUTRAL")
    z_score: float = 0.0
    percent_b: float = 0.5
    hurst: float = 0.5
    half_life_bars: float = 0.0
    is_mean_reverting: bool = False
    entry_zone: bool = False
    strength: float = 0.0
    target: float = 0.0
    stop: float = 0.0
    reason: str = ""

    @field_validator("bias")
    @classmethod
    def _b(cls, v):
        v = v.upper()
        return v if v in ("BUY", "SELL", "NEUTRAL") else "NEUTRAL"


def _hurst(series: np.ndarray, max_lag: int = 20) -> float:
    """
    Quick Hurst exponent estimate via R/S analysis (rolling).
    Returns 0.5 if insufficient data. H < 0.5 = mean-reverting.
    """
    if len(series) < max_lag * 2:
        return 0.5
    lags = range(2, max_lag)
    tau = []
    for lag in lags:
        d = np.subtract(series[lag:], series[:-lag])
        s = np.std(d)
        if s <= 0 or np.isnan(s):
            return 0.5
        tau.append(s)
    if len(tau) < 2:
        return 0.5
    poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
    return float(np.clip(poly[0], 0.0, 1.0))


def _half_life(series: np.ndarray) -> float:
    """
    Ornstein-Uhlenbeck half-life via OLS on Δy = a + b·y_{t-1}.
    Negative or unstable b → return 0.
    """
    if len(series) < 30:
        return 0.0
    y = series[1:]
    y_lag = series[:-1]
    dy = y - y_lag
    # OLS
    x = np.vstack([np.ones_like(y_lag), y_lag]).T
    try:
        beta, _resid, _r, _s = np.linalg.lstsq(x, dy, rcond=None)
        b = beta[1]
        if b >= 0:
            return 0.0
        return float(-np.log(2) / b)
    except Exception:
        return 0.0


class StatArbAgent(BaseAgent):
    name = "StatArbAgent"

    def __init__(self, config=None, window: int = 50, entry_z: float = 2.0):
        super().__init__(config)
        self.model = "pure-python"
        self._feed = DataFeed()
        self._window = window
        self._entry_z = entry_z

    def _default_output(self) -> StatArbSignal:
        return StatArbSignal(reason="Stat-arb agent unavailable")

    async def analyze(self, snapshot: Optional[MarketSnapshot] = None, **kw) -> StatArbSignal:
        if snapshot is None:
            snapshot = self._feed.get_snapshot()
        # Use 15m timeframe if available, fallback to 1h
        try:
            df = self._feed.get_ohlcv_dataframe("15m")
            if df.empty or len(df) < self._window:
                df = self._feed.get_ohlcv_dataframe("1h")
        except Exception:
            df = self._feed.get_ohlcv_dataframe("1h")

        if df.empty or len(df) < self._window:
            return StatArbSignal(reason="Insufficient data")

        close = df["close"].astype(float).to_numpy()
        roll = pd.Series(close).rolling(self._window)
        mu = roll.mean().iloc[-1]
        sd = roll.std(ddof=1).iloc[-1]
        if sd is None or np.isnan(sd) or sd <= 0:
            return StatArbSignal(reason="Std-dev degenerate")

        z = float((close[-1] - mu) / sd)

        # Bollinger %B (price relative to ±2σ band)
        upper = mu + 2 * sd
        lower = mu - 2 * sd
        pct_b = float((close[-1] - lower) / (upper - lower)) if upper > lower else 0.5

        h = _hurst(close[-200:] if len(close) >= 200 else close)
        hl = _half_life(close[-200:] if len(close) >= 200 else close)
        is_mr = h < 0.5 and hl > 0.0

        bias = "NEUTRAL"
        strength = 0.0
        target = float(mu)
        stop = close[-1]

        if is_mr:
            if z <= -self._entry_z:
                bias = "BUY"   # fade the dip
                strength = float(min(1.0, abs(z) / 3.5))
                stop = float(close[-1] - 1.5 * sd)
            elif z >= self._entry_z:
                bias = "SELL"  # fade the rip
                strength = float(min(1.0, abs(z) / 3.5))
                stop = float(close[-1] + 1.5 * sd)

        reason = (
            f"z={z:+.2f} pct_b={pct_b:.2f} H={h:.2f} hl={hl:.1f} "
            f"(MR={is_mr}, entry_z=±{self._entry_z})"
        )
        self.logger.info(f"StatArb: bias={bias} str={strength:.2f} | {reason}")
        return StatArbSignal(
            bias=bias,
            z_score=round(z, 3),
            percent_b=round(pct_b, 3),
            hurst=round(h, 3),
            half_life_bars=round(hl, 2),
            is_mean_reverting=is_mr,
            entry_zone=bias != "NEUTRAL",
            strength=round(strength, 3),
            target=round(target, 5),
            stop=round(stop, 5),
            reason=reason,
        )
