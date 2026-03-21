"""
agents/technical.py — Technical Analysis Agent
Pure Python indicators (EMA, RSI, MACD, ATR) + optional LLM interpretation.
Outputs trend direction, entry signal, and ATR-based SL/TP levels.
"""
from __future__ import annotations

from typing import Optional, Tuple
import numpy as np
import pandas as pd

from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from data.data_feed import DataFeed, MarketSnapshot
from utils.risk_utils import calculate_atr_sl_tp


# ── Indicator Calculations (pure Python / pandas / numpy) ─────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple:
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ── Output Schema ──────────────────────────────────────────────────────────────

class TechnicalSignal(BaseModel):
    trend: str = Field(description="UP | DOWN | SIDEWAYS")
    entry_signal: bool = Field(description="True if there is a valid entry signal")
    signal_direction: str = Field(description="BUY | SELL | NONE")
    strength: float = Field(description="Signal strength 0.0 to 1.0")
    ema_50: float = Field(default=0.0)
    ema_200: float = Field(default=0.0)
    rsi: float = Field(default=50.0)
    macd_histogram: float = Field(default=0.0)
    atr: float = Field(default=0.0001)
    suggested_sl: float = Field(default=0.0)
    suggested_tp: float = Field(default=0.0)
    reason: str = Field(default="")

    @field_validator("trend")
    @classmethod
    def validate_trend(cls, v: str) -> str:
        v = v.upper()
        return v if v in ("UP", "DOWN", "SIDEWAYS") else "SIDEWAYS"

    @field_validator("signal_direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        v = v.upper()
        return v if v in ("BUY", "SELL", "NONE") else "NONE"

    @field_validator("strength")
    @classmethod
    def clamp_strength(cls, v: float) -> float:
        return max(0.0, min(1.0, round(v, 3)))


# ── Agent ─────────────────────────────────────────────────────────────────────

class TechnicalAgent(BaseAgent):
    name = "TechnicalAgent"

    def __init__(self, config=None):
        super().__init__(config)
        self.model = self.config.ollama_model_technical
        self._feed = DataFeed()

    def _default_output(self) -> TechnicalSignal:
        return TechnicalSignal(
            trend="SIDEWAYS",
            entry_signal=False,
            signal_direction="NONE",
            strength=0.0,
            reason="Technical agent unavailable — defaulting to SIDEWAYS/NONE",
        )

    async def analyze(
        self,
        snapshot: Optional[MarketSnapshot] = None,
        **kwargs,
    ) -> TechnicalSignal:
        """
        Calculate indicators on real OHLCV data and derive a technical signal.
        Pure Python — no LLM required (LLM used only for verbal commentary if Ollama up).
        """
        if snapshot is None:
            snapshot = self._feed.get_snapshot()

        df = self._feed.get_ohlcv_dataframe("1h")
        entry_price = snapshot.mid

        if df.empty or len(df) < 50:
            self.logger.warning("Insufficient OHLCV data — returning neutral signal")
            return self._default_output()

        close = df["close"]

        # ── Indicators ───────────────────────────────────────────────────────
        ema_50 = _ema(close, 50).iloc[-1]
        ema_200 = _ema(close, 200).iloc[-1] if len(close) >= 200 else _ema(close, len(close)).iloc[-1]
        rsi_val = _rsi(close, 14).iloc[-1]
        macd_line, signal_line, histogram = _macd(close)
        macd_hist = histogram.iloc[-1]
        atr_val = _atr(df, 14).iloc[-1]

        if pd.isna(atr_val) or atr_val <= 0:
            atr_val = entry_price * 0.003  # 0.3% fallback

        # ── Trend Detection via EMA Cross ─────────────────────────────────
        if ema_50 > ema_200 * 1.0001:  # Golden cross territory
            trend = "UP"
        elif ema_50 < ema_200 * 0.9999:  # Death cross territory
            trend = "DOWN"
        else:
            trend = "SIDEWAYS"

        # ── Entry Signal Logic ────────────────────────────────────────────
        # BUY conditions: trend UP + RSI 40-65 + MACD positive + price > EMA50
        buy_conditions = [
            trend == "UP",
            40 <= rsi_val <= 65,
            macd_hist > 0,
            entry_price > ema_50,
        ]
        # SELL conditions: trend DOWN + RSI 35-60 + MACD negative + price < EMA50
        sell_conditions = [
            trend == "DOWN",
            35 <= rsi_val <= 60,
            macd_hist < 0,
            entry_price < ema_50,
        ]

        buy_score = sum(buy_conditions) / len(buy_conditions)
        sell_score = sum(sell_conditions) / len(sell_conditions)

        if buy_score >= 0.75:
            signal_dir = "BUY"
            strength = round(buy_score, 3)
            entry_signal = True
        elif sell_score >= 0.75:
            signal_dir = "SELL"
            strength = round(sell_score, 3)
            entry_signal = True
        else:
            signal_dir = "NONE"
            strength = max(buy_score, sell_score)
            entry_signal = False

        # ── ATR-based SL/TP ──────────────────────────────────────────────
        if signal_dir in ("BUY", "SELL"):
            sl, tp = calculate_atr_sl_tp(entry_price, atr_val, signal_dir)
        else:
            sl, tp = 0.0, 0.0

        # ── RSI warnings ─────────────────────────────────────────────────
        rsi_note = ""
        if rsi_val > 70:
            rsi_note = "RSI overbought — caution on BUY. "
            if signal_dir == "BUY":
                strength *= 0.5
        elif rsi_val < 30:
            rsi_note = "RSI oversold — caution on SELL. "
            if signal_dir == "SELL":
                strength *= 0.5

        reason = (
            f"EMA50={ema_50:.5f}, EMA200={ema_200:.5f}, "
            f"RSI={rsi_val:.1f}, MACD_hist={macd_hist:.6f}, "
            f"ATR={atr_val:.5f}. {rsi_note}"
            f"Trend={trend}, Signal={signal_dir}({strength:.0%})"
        )

        self.logger.info(f"Technical: {trend} | {signal_dir} | strength={strength:.2f} | RSI={rsi_val:.1f}")

        return TechnicalSignal(
            trend=trend,
            entry_signal=entry_signal,
            signal_direction=signal_dir,
            strength=round(strength, 3),
            ema_50=round(ema_50, 5),
            ema_200=round(ema_200, 5),
            rsi=round(rsi_val, 2),
            macd_histogram=round(macd_hist, 7),
            atr=round(atr_val, 6),
            suggested_sl=sl,
            suggested_tp=tp,
            reason=reason,
        )
