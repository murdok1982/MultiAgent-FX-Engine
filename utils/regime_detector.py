"""Market regime detection using statistical analysis of OHLCV data."""
from __future__ import annotations
from enum import Enum
from typing import TYPE_CHECKING
import numpy as np

if TYPE_CHECKING:
    from data.data_feed import OHLCV


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


def calculate_adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """Average Directional Index (Wilder smoothed)."""
    n = len(close)
    if n < period + 1:
        return np.zeros(n)

    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        hl = high[i] - low[i]
        hpc = abs(high[i] - close[i - 1])
        lpc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hpc, lpc)

        up_move = high[i] - high[i - 1]
        down_move = low[i - 1] - low[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0.0

    def wilder_smooth(arr: np.ndarray) -> np.ndarray:
        smoothed = np.zeros(n)
        smoothed[period] = arr[1 : period + 1].sum()
        for i in range(period + 1, n):
            smoothed[i] = smoothed[i - 1] - (smoothed[i - 1] / period) + arr[i]
        return smoothed

    tr_s = wilder_smooth(tr)
    plus_dm_s = wilder_smooth(plus_dm)
    minus_dm_s = wilder_smooth(minus_dm)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = np.where(tr_s > 0, 100 * plus_dm_s / tr_s, 0.0)
        minus_di = np.where(tr_s > 0, 100 * minus_dm_s / tr_s, 0.0)
        di_sum = plus_di + minus_di
        dx = np.where(di_sum > 0, 100 * np.abs(plus_di - minus_di) / di_sum, 0.0)

    adx = np.zeros(n)
    if 2 * period < n:
        adx[2 * period] = dx[period : 2 * period + 1].mean()
        for i in range(2 * period + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return adx


def calculate_bollinger_bands(
    close: np.ndarray,
    period: int = 20,
    std_dev: float = 2.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Returns (upper_band, middle_band, lower_band)."""
    n = len(close)
    upper = np.full(n, np.nan)
    middle = np.full(n, np.nan)
    lower = np.full(n, np.nan)

    for i in range(period - 1, n):
        window = close[i - period + 1 : i + 1]
        ma = window.mean()
        std = window.std(ddof=0)
        middle[i] = ma
        upper[i] = ma + std_dev * std
        lower[i] = ma - std_dev * std

    return upper, middle, lower


def detect_regime(
    ohlcv: list,  # List[OHLCV]
    lookback: int = 50,
) -> tuple[MarketRegime, float]:
    """
    Detect the current market regime.

    Returns (regime, confidence) where confidence is in [0.0, 1.0].

    Algorithm:
    1. ADX > 25 → trending; direction from EMA slope
    2. ADX < 20 → ranging
    3. ATR at 90th percentile of last lookback → volatile
    """
    if len(ohlcv) < lookback:
        return MarketRegime.UNKNOWN, 0.0

    candles = ohlcv[-lookback:]
    high = np.array([c.high for c in candles])
    low = np.array([c.low for c in candles])
    close = np.array([c.close for c in candles])

    # ADX
    adx_series = calculate_adx(high, low, close, period=14)
    adx = adx_series[-1]

    # EMA slope for trend direction (EMA20 slope over last 5 bars)
    ema_period = min(20, len(candles) - 1)
    weights = np.exp(np.linspace(-1.0, 0.0, ema_period))
    weights /= weights.sum()
    ema_recent = np.convolve(close, weights[::-1], mode="valid")[-5:]
    trend_up = bool(ema_recent[-1] > ema_recent[0]) if len(ema_recent) >= 2 else True

    # ATR
    tr = np.maximum(high[1:] - low[1:], np.maximum(
        np.abs(high[1:] - close[:-1]),
        np.abs(low[1:] - close[:-1]),
    ))
    atr_current = tr[-14:].mean() if len(tr) >= 14 else tr.mean()
    atr_90th = np.percentile(tr, 90) if len(tr) > 0 else atr_current

    # Bollinger squeeze (ranging indicator) — computed but used as supporting signal
    _, bb_mid, _ = calculate_bollinger_bands(close, period=20)
    valid_bb = bb_mid[~np.isnan(bb_mid)]  # noqa: F841  kept for future use

    # Decision logic
    if atr_current > atr_90th * 1.2:
        # Extreme volatility
        confidence = min(1.0, (atr_current - atr_90th) / atr_90th)
        return MarketRegime.VOLATILE, round(confidence, 3)

    if adx > 25:
        confidence = min(1.0, (adx - 25) / 25)
        regime = MarketRegime.TRENDING_UP if trend_up else MarketRegime.TRENDING_DOWN
        return regime, round(confidence, 3)

    if adx < 20:
        confidence = min(1.0, (20 - adx) / 20)
        return MarketRegime.RANGING, round(confidence, 3)

    return MarketRegime.UNKNOWN, 0.3
