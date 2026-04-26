"""
agents/technical.py — Technical Analysis Agent (Multi-Timeframe)
Pure Python indicators (EMA, RSI, MACD, ATR, Bollinger Bands) with multi-timeframe
confluence, market regime detection, and Forex session filtering.
Outputs trend direction, entry signal, ATR-based adaptive SL/TP levels, and
regime/session metadata for downstream agents.
"""
from __future__ import annotations

from typing import List, Optional, Tuple
import numpy as np
import pandas as pd

from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from data.data_feed import DataFeed, MarketSnapshot, OHLCV
from utils.risk_utils import calculate_atr_sl_tp
from utils.session_filter import (
    get_current_session,
    get_session_quality_multiplier,
    is_trading_allowed_by_session,
)
from utils.regime_detector import (
    MarketRegime,
    calculate_bollinger_bands,
    detect_regime,
)


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
    # --- CAMPOS EXISTENTES (mantener compatibilidad) ---
    trend: str = Field(default="SIDEWAYS", description="UP | DOWN | SIDEWAYS")
    entry_signal: bool = Field(default=False, description="True if there is a valid entry signal")
    signal_direction: str = Field(default="NONE", description="BUY | SELL | NONE")
    strength: float = Field(default=0.0, description="Signal strength 0.0 to 1.0")
    ema_50: float = Field(default=0.0)
    ema_200: float = Field(default=0.0)
    rsi: float = Field(default=50.0)
    macd_histogram: float = Field(default=0.0)
    atr: float = Field(default=0.0001)
    suggested_sl: float = Field(default=0.0)
    suggested_tp: float = Field(default=0.0)
    reason: str = Field(default="")

    # --- CAMPOS NUEVOS (todos con default para no romper código existente) ---
    regime: str = Field(default="UNKNOWN", description="TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE | UNKNOWN")
    regime_confidence: float = Field(default=0.0)
    session: str = Field(default="UNKNOWN", description="ASIAN | EUROPEAN | AMERICAN | LONDON_NY_OVERLAP | OFF_HOURS")
    session_quality: float = Field(default=1.0, description="Session quality multiplier 0.0–1.0")

    # Multi-timeframe confluence
    trend_15m: str = Field(default="SIDEWAYS", description="UP | DOWN | SIDEWAYS")
    trend_1h: str = Field(default="SIDEWAYS", description="UP | DOWN | SIDEWAYS")
    trend_4h: str = Field(default="SIDEWAYS", description="UP | DOWN | SIDEWAYS (approximated from 1h bars)")
    timeframe_confluence: float = Field(default=0.0, description="Fraction of timeframes aligned 0.0–1.0")

    # Bollinger Bands
    bb_upper: float = Field(default=0.0)
    bb_middle: float = Field(default=0.0)
    bb_lower: float = Field(default=0.0)
    bb_position: float = Field(default=0.0, description="-1.0 (at lower band) to +1.0 (at upper band)")

    # Support / Resistance
    support_level: float = Field(default=0.0)
    resistance_level: float = Field(default=0.0)

    # SL/TP multipliers used
    sl_multiplier: float = Field(default=1.5)
    tp_multiplier: float = Field(default=2.5)

    @field_validator("trend", "trend_15m", "trend_1h", "trend_4h")
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

    # ── Private helpers ───────────────────────────────────────────────────────

    def _calc_trend_for_tf(self, ohlcv_list: List[OHLCV]) -> str:
        """
        Calculate trend (UP/DOWN/SIDEWAYS) for a given timeframe.
        Uses EMA20 and EMA50 cross when enough data is available,
        otherwise falls back to simple price direction comparison.
        """
        if not ohlcv_list or len(ohlcv_list) < 5:
            return "SIDEWAYS"

        closes = pd.Series([c.close for c in ohlcv_list])

        if len(closes) >= 50:
            ema20 = _ema(closes, 20).iloc[-1]
            ema50 = _ema(closes, 50).iloc[-1]
            if ema20 > ema50 * 1.0001:
                return "UP"
            if ema20 < ema50 * 0.9999:
                return "DOWN"
            return "SIDEWAYS"

        # Fallback: short EMA direction
        short_period = min(10, len(closes) - 1)
        if short_period < 2:
            # Ultra minimal: compare first vs last close
            if closes.iloc[-1] > closes.iloc[0] * 1.0003:
                return "UP"
            if closes.iloc[-1] < closes.iloc[0] * 0.9997:
                return "DOWN"
            return "SIDEWAYS"

        ema_short = _ema(closes, short_period)
        slope = ema_short.iloc[-1] - ema_short.iloc[-min(5, len(ema_short))]
        threshold = closes.iloc[-1] * 0.0002  # 2 pips
        if slope > threshold:
            return "UP"
        if slope < -threshold:
            return "DOWN"
        return "SIDEWAYS"

    def _calc_timeframe_confluence(
        self,
        trend_15m: str,
        trend_1h: str,
        trend_4h: str,
    ) -> float:
        """
        Fraction of timeframes aligned in the same direction.
        All UP → 1.0, 2 of 3 UP → 0.67, 1 → 0.33, mixed → 0.0.
        SIDEWAYS does not count toward either direction.
        """
        trends = [trend_15m, trend_1h, trend_4h]
        up_count = trends.count("UP")
        down_count = trends.count("DOWN")
        total = len(trends)
        dominant = max(up_count, down_count)
        return round(dominant / total, 3)

    def _get_4h_trend(self, ohlcv_1h: List[OHLCV]) -> str:
        """Approximate 4h trend by grouping the last 4 hourly candles."""
        if len(ohlcv_1h) < 4:
            return "SIDEWAYS"
        last_4 = ohlcv_1h[-4:]
        open_price = last_4[0].open
        close_price = last_4[-1].close
        threshold = last_4[-1].close * 0.0003  # 3 pips
        if close_price > open_price + threshold:
            return "UP"
        if close_price < open_price - threshold:
            return "DOWN"
        return "SIDEWAYS"

    def _get_sl_tp_multipliers(self, regime: str) -> Tuple[float, float]:
        """
        Adaptive SL/TP multipliers by market regime:
          TRENDING:  SL=1.5×ATR, TP=3.0×ATR  (let winners run)
          RANGING:   SL=1.0×ATR, TP=1.5×ATR  (take profits quickly)
          VOLATILE:  SL=2.0×ATR, TP=2.0×ATR  (wide stops)
          UNKNOWN:   SL=1.5×ATR, TP=2.5×ATR  (conservative default)
        """
        regime_map: dict[str, Tuple[float, float]] = {
            MarketRegime.TRENDING_UP.value:   (1.5, 3.0),
            MarketRegime.TRENDING_DOWN.value: (1.5, 3.0),
            MarketRegime.RANGING.value:       (1.0, 1.5),
            MarketRegime.VOLATILE.value:      (2.0, 2.0),
            MarketRegime.UNKNOWN.value:       (1.5, 2.5),
        }
        return regime_map.get(regime, (1.5, 2.5))

    # ── Main analysis entry point ─────────────────────────────────────────────

    async def analyze(
        self,
        snapshot: Optional[MarketSnapshot] = None,
        **kwargs,
    ) -> TechnicalSignal:
        """
        Multi-timeframe technical analysis with session filtering and regime detection.
        Pure Python — no LLM required (Ollama used only for verbal commentary if available).
        """
        if snapshot is None:
            snapshot = self._feed.get_snapshot()

        entry_price = snapshot.mid

        # ── Session context ───────────────────────────────────────────────────
        current_session = get_current_session()
        session_quality = get_session_quality_multiplier()
        trading_allowed = is_trading_allowed_by_session()

        # ── Build primary 1h DataFrame ────────────────────────────────────────
        df = self._feed.get_ohlcv_dataframe("1h")
        if df.empty or len(df) < 50:
            self.logger.warning("Insufficient 1h OHLCV data — returning neutral signal")
            return TechnicalSignal(
                trend="SIDEWAYS",
                entry_signal=False,
                signal_direction="NONE",
                strength=0.0,
                session=current_session.value,
                session_quality=session_quality,
                reason="Insufficient 1h data — returning neutral signal",
            )

        close_1h = df["close"]
        ohlcv_1h: List[OHLCV] = snapshot.ohlcv_1h
        ohlcv_15m: List[OHLCV] = snapshot.ohlcv_15m or []

        # ── Core indicators on 1h ─────────────────────────────────────────────
        ema_50 = _ema(close_1h, 50).iloc[-1]
        ema_200 = (
            _ema(close_1h, 200).iloc[-1]
            if len(close_1h) >= 200
            else _ema(close_1h, len(close_1h)).iloc[-1]
        )
        rsi_val = _rsi(close_1h, 14).iloc[-1]
        macd_line, signal_line, histogram = _macd(close_1h)
        macd_hist = histogram.iloc[-1]
        atr_val = _atr(df, 14).iloc[-1]

        if pd.isna(atr_val) or atr_val <= 0:
            atr_val = entry_price * 0.003  # 0.3% fallback

        # ── Bollinger Bands (1h closes) ───────────────────────────────────────
        close_np = close_1h.to_numpy(dtype=float)
        bb_upper_arr, bb_mid_arr, bb_lower_arr = calculate_bollinger_bands(
            close_np, period=20, std_dev=2.0
        )
        bb_upper = float(bb_upper_arr[-1]) if not np.isnan(bb_upper_arr[-1]) else entry_price
        bb_middle = float(bb_mid_arr[-1]) if not np.isnan(bb_mid_arr[-1]) else entry_price
        bb_lower = float(bb_lower_arr[-1]) if not np.isnan(bb_lower_arr[-1]) else entry_price

        # BB position: -1.0 at lower, 0.0 at middle, +1.0 at upper
        bb_range = bb_upper - bb_lower
        if bb_range > 0:
            bb_position = 2.0 * (entry_price - bb_lower) / bb_range - 1.0
            bb_position = max(-1.0, min(1.0, bb_position))
        else:
            bb_position = 0.0

        # ── Support / Resistance (1h, last 20 candles) ───────────────────────
        if len(ohlcv_1h) >= 20:
            recent_20 = ohlcv_1h[-20:]
            support_level = min(c.low for c in recent_20)
            resistance_level = max(c.high for c in recent_20)
        else:
            support_level = entry_price * 0.995
            resistance_level = entry_price * 1.005

        # ── Market Regime Detection (uses 1h data) ────────────────────────────
        regime_enum, regime_conf = detect_regime(ohlcv_1h, lookback=50)
        regime_str = regime_enum.value

        # ── Multi-Timeframe Trends ────────────────────────────────────────────
        trend_15m = self._calc_trend_for_tf(ohlcv_15m)
        trend_1h = self._calc_trend_for_tf(ohlcv_1h)
        trend_4h = self._get_4h_trend(ohlcv_1h)
        timeframe_confluence = self._calc_timeframe_confluence(trend_15m, trend_1h, trend_4h)

        # ── Primary Trend (1h EMA cross) ──────────────────────────────────────
        if ema_50 > ema_200 * 1.0001:
            trend = "UP"
        elif ema_50 < ema_200 * 0.9999:
            trend = "DOWN"
        else:
            trend = "SIDEWAYS"

        # ── Entry Signal Conditions ───────────────────────────────────────────
        # BUY: trend UP + RSI 40–65 + MACD positive + price > EMA50
        buy_conditions = [
            trend == "UP",
            40 <= rsi_val <= 65,
            macd_hist > 0,
            entry_price > ema_50,
        ]
        # SELL: trend DOWN + RSI 35–60 + MACD negative + price < EMA50
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
            raw_strength = buy_score
            entry_signal = True
        elif sell_score >= 0.75:
            signal_dir = "SELL"
            raw_strength = sell_score
            entry_signal = True
        else:
            signal_dir = "NONE"
            raw_strength = max(buy_score, sell_score)
            entry_signal = False

        # ── RSI Overbought / Oversold Penalty ─────────────────────────────────
        rsi_note = ""
        if rsi_val > 70:
            rsi_note = "RSI overbought — caution on BUY. "
            if signal_dir == "BUY":
                raw_strength *= 0.5
        elif rsi_val < 30:
            rsi_note = "RSI oversold — caution on SELL. "
            if signal_dir == "SELL":
                raw_strength *= 0.5

        # ── Composite Strength Calculation ────────────────────────────────────
        # 1. Apply session quality multiplier
        strength = raw_strength * session_quality
        # 2. Scale by timeframe confluence (minimum 50% base)
        strength = strength * (0.5 + 0.5 * timeframe_confluence)
        # 3. Regime confirmation bonus (+0.1, capped at 1.0)
        regime_confirms = (
            (signal_dir == "BUY" and regime_str == MarketRegime.TRENDING_UP.value)
            or (signal_dir == "SELL" and regime_str == MarketRegime.TRENDING_DOWN.value)
        )
        if regime_confirms:
            strength = min(1.0, strength + 0.1)
        # 4. OFF_HOURS always kills entry signal
        if not trading_allowed:
            entry_signal = False

        strength = round(strength, 3)

        # ── Adaptive SL/TP by Regime ──────────────────────────────────────────
        sl_mult, tp_mult = self._get_sl_tp_multipliers(regime_str)
        if signal_dir in ("BUY", "SELL"):
            sl, tp = calculate_atr_sl_tp(entry_price, atr_val, signal_dir, sl_mult, tp_mult)
        else:
            sl, tp = 0.0, 0.0

        # ── Reason String ─────────────────────────────────────────────────────
        reason = (
            f"EMA50={ema_50:.5f}, EMA200={ema_200:.5f}, "
            f"RSI={rsi_val:.1f}, MACD_hist={macd_hist:.6f}, "
            f"ATR={atr_val:.5f}. {rsi_note}"
            f"Trend={trend}(1h), 15m={trend_15m}, 4h={trend_4h}, "
            f"Confluence={timeframe_confluence:.0%}, "
            f"Regime={regime_str}({regime_conf:.0%}), "
            f"Session={current_session.value}(q={session_quality:.2f}), "
            f"SL×{sl_mult}/TP×{tp_mult}, "
            f"Signal={signal_dir}({strength:.0%})"
        )

        self.logger.info(
            f"Technical: {trend} | {signal_dir} | strength={strength:.2f} | "
            f"RSI={rsi_val:.1f} | regime={regime_str} | session={current_session.value} | "
            f"confluence={timeframe_confluence:.0%}"
        )

        return TechnicalSignal(
            # Existing fields
            trend=trend,
            entry_signal=entry_signal,
            signal_direction=signal_dir,
            strength=strength,
            ema_50=round(ema_50, 5),
            ema_200=round(ema_200, 5),
            rsi=round(rsi_val, 2),
            macd_histogram=round(macd_hist, 7),
            atr=round(atr_val, 6),
            suggested_sl=sl,
            suggested_tp=tp,
            reason=reason,
            # New fields
            regime=regime_str,
            regime_confidence=regime_conf,
            session=current_session.value,
            session_quality=session_quality,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            trend_4h=trend_4h,
            timeframe_confluence=timeframe_confluence,
            bb_upper=round(bb_upper, 5),
            bb_middle=round(bb_middle, 5),
            bb_lower=round(bb_lower, 5),
            bb_position=round(bb_position, 3),
            support_level=round(support_level, 5),
            resistance_level=round(resistance_level, 5),
            sl_multiplier=sl_mult,
            tp_multiplier=tp_mult,
        )
