"""
agents/order_flow.py — Order Flow / Volume Profile / ICT Agent
==============================================================

Implements Smart Money Concepts (Inner Circle Trader) detection on OHLCV:

  • Order Blocks (OB)        — last opposing candle before strong impulse
  • Fair Value Gaps (FVG)    — 3-candle imbalance zones
  • Break of Structure (BOS) — break of last swing high/low in trend direction
  • Change of Character (CHoCH) — first counter-trend swing break
  • Liquidity Sweeps         — wicks beyond recent equal highs/lows
  • Volume Profile           — POC, VAH, VAL (volume-at-price histogram)
  • Premium / Discount zones — relative to recent dealing range

Output is a directional bias with confluence score 0–1.
Pure Python: no LLM required (LLM optional for commentary).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from data.data_feed import DataFeed, MarketSnapshot, OHLCV


# ── Schemas ───────────────────────────────────────────────────────────────────

class OrderBlock(BaseModel):
    side: str          # BULLISH | BEARISH
    top: float
    bottom: float
    timestamp_idx: int
    mitigated: bool = False


class FairValueGap(BaseModel):
    side: str          # BULLISH | BEARISH
    top: float
    bottom: float
    idx: int
    filled: bool = False


class OrderFlowSignal(BaseModel):
    bias: str = Field(default="NEUTRAL", description="BULLISH | BEARISH | NEUTRAL")
    confluence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    structure: str = Field(default="UNDEFINED",
                           description="BOS_UP | BOS_DOWN | CHOCH_UP | CHOCH_DOWN | RANGE")
    in_premium: bool = False        # current price in premium zone (sell area)
    in_discount: bool = False       # current price in discount zone (buy area)
    liquidity_swept: str = Field(default="NONE", description="HIGH | LOW | NONE")
    poc: float = 0.0                # Point of Control (volume profile)
    vah: float = 0.0                # Value Area High
    val: float = 0.0                # Value Area Low
    active_order_blocks: int = 0
    active_fvgs: int = 0
    nearest_demand: float = 0.0
    nearest_supply: float = 0.0
    reason: str = ""

    @field_validator("bias")
    @classmethod
    def _bias(cls, v):
        v = v.upper()
        return v if v in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL"


# ── Detection Helpers ─────────────────────────────────────────────────────────

def _swings(close: np.ndarray, high: np.ndarray, low: np.ndarray, k: int = 3
           ) -> Tuple[List[int], List[int]]:
    """Pivot detection: a swing high/low has k bars lower/higher on each side."""
    n = len(close)
    sh, sl = [], []
    for i in range(k, n - k):
        window_high = high[i - k:i + k + 1]
        window_low = low[i - k:i + k + 1]
        if high[i] == window_high.max() and high[i] > window_high[:k].max():
            sh.append(i)
        if low[i] == window_low.min() and low[i] < window_low[:k].min():
            sl.append(i)
    return sh, sl


def _detect_order_blocks(df: pd.DataFrame, lookback: int = 80) -> List[OrderBlock]:
    """
    A bullish OB = last bearish candle before an impulse that breaks the most
    recent swing high. Mirrored for bearish.
    Impulse threshold: candle range >= 1.5 × ATR(14).
    """
    if len(df) < 30:
        return []
    n = len(df)
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()

    tr = np.maximum(h[1:] - l[1:], np.maximum(
        np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])
    ))
    atr = pd.Series(tr).rolling(14).mean().to_numpy()
    atr = np.concatenate([[atr[0]], atr])  # align length

    blocks: List[OrderBlock] = []
    start = max(2, n - lookback)
    for i in range(start, n - 2):
        rng = h[i + 1] - l[i + 1]
        if np.isnan(atr[i]) or atr[i] <= 0:
            continue
        if rng < 1.5 * atr[i]:
            continue
        # Bullish impulse i+1 (close above its open by big amount)
        if c[i + 1] > o[i + 1] and (c[i + 1] - o[i + 1]) > 0.6 * rng:
            # Find last bearish candle at/before i
            for j in range(i, max(i - 8, 0), -1):
                if c[j] < o[j]:
                    blocks.append(OrderBlock(
                        side="BULLISH",
                        top=float(max(o[j], c[j])),
                        bottom=float(l[j]),
                        timestamp_idx=int(j),
                    ))
                    break
        # Bearish impulse
        elif c[i + 1] < o[i + 1] and (o[i + 1] - c[i + 1]) > 0.6 * rng:
            for j in range(i, max(i - 8, 0), -1):
                if c[j] > o[j]:
                    blocks.append(OrderBlock(
                        side="BEARISH",
                        top=float(h[j]),
                        bottom=float(min(o[j], c[j])),
                        timestamp_idx=int(j),
                    ))
                    break
    # Mark mitigated (price returned into the zone after creation)
    last_close = c[-1]
    for b in blocks:
        # Mitigation = current price has visited the OB at least once since
        post_high = h[b.timestamp_idx + 1:].max() if b.timestamp_idx + 1 < n else last_close
        post_low = l[b.timestamp_idx + 1:].min() if b.timestamp_idx + 1 < n else last_close
        if b.side == "BULLISH" and post_low <= b.top:
            b.mitigated = True
        if b.side == "BEARISH" and post_high >= b.bottom:
            b.mitigated = True
    return blocks


def _detect_fvgs(df: pd.DataFrame, lookback: int = 100) -> List[FairValueGap]:
    """
    Bullish FVG: gap between low[i] and high[i-2] (3-candle pattern, i is current).
      Condition: low[i] > high[i-2] → bullish gap (low[i-2], high[i-2], i bullish impulse).
    Bearish FVG: high[i] < low[i-2].
    """
    n = len(df)
    if n < 3:
        return []
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    fvgs: List[FairValueGap] = []
    start = max(2, n - lookback)
    for i in range(start, n):
        if l[i] > h[i - 2]:
            fvgs.append(FairValueGap(
                side="BULLISH",
                bottom=float(h[i - 2]),
                top=float(l[i]),
                idx=int(i),
            ))
        elif h[i] < l[i - 2]:
            fvgs.append(FairValueGap(
                side="BEARISH",
                bottom=float(h[i]),
                top=float(l[i - 2]),
                idx=int(i),
            ))
    last_price = df["close"].iloc[-1]
    for g in fvgs:
        if g.side == "BULLISH" and last_price < g.bottom:
            g.filled = True
        if g.side == "BEARISH" and last_price > g.top:
            g.filled = True
    return fvgs


def _detect_structure(df: pd.DataFrame) -> Tuple[str, Optional[float], Optional[float]]:
    """
    Identify BOS or CHoCH on the last swing.
    Returns (label, last_swing_high, last_swing_low).
    """
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    sh_idx, sl_idx = _swings(c, h, l, k=3)
    if len(sh_idx) < 2 or len(sl_idx) < 2:
        return "RANGE", None, None

    last_h = h[sh_idx[-1]]
    prev_h = h[sh_idx[-2]]
    last_l = l[sl_idx[-1]]
    prev_l = l[sl_idx[-2]]
    price = c[-1]

    # Bullish trend = HH and HL
    hh = last_h > prev_h
    hl = last_l > prev_l
    lh = last_h < prev_h
    ll = last_l < prev_l

    if price > last_h and hh:
        return "BOS_UP", float(last_h), float(last_l)
    if price < last_l and ll:
        return "BOS_DOWN", float(last_h), float(last_l)
    if price > last_h and (lh or ll):
        return "CHOCH_UP", float(last_h), float(last_l)
    if price < last_l and (hh or hl):
        return "CHOCH_DOWN", float(last_h), float(last_l)
    return "RANGE", float(last_h), float(last_l)


def _liquidity_sweep(df: pd.DataFrame, lookback: int = 20) -> str:
    """Detect a wick sweep beyond recent equal highs/lows that closed back inside."""
    if len(df) < lookback + 1:
        return "NONE"
    recent = df.iloc[-lookback - 1: -1]
    last = df.iloc[-1]
    rh = recent["high"].max()
    rl = recent["low"].min()
    swept_high = last["high"] > rh and last["close"] < rh
    swept_low = last["low"] < rl and last["close"] > rl
    if swept_high:
        return "HIGH"
    if swept_low:
        return "LOW"
    return "NONE"


def _volume_profile(df: pd.DataFrame, bins: int = 40, value_area_pct: float = 0.68
                   ) -> Tuple[float, float, float]:
    """Return (POC, VAH, VAL) using close-price bucketing weighted by volume."""
    if df.empty:
        return 0.0, 0.0, 0.0
    close = df["close"].to_numpy()
    vol = df.get("volume", pd.Series(np.ones(len(df)))).to_numpy()
    if vol.sum() == 0:
        vol = np.ones(len(close))
    lo, hi = close.min(), close.max()
    if hi <= lo:
        return float(close[-1]), float(close[-1]), float(close[-1])
    edges = np.linspace(lo, hi, bins + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    hist = np.zeros(bins)
    idx = np.clip(np.searchsorted(edges, close, side="right") - 1, 0, bins - 1)
    for i, v in zip(idx, vol):
        hist[i] += v
    poc_idx = int(hist.argmax())
    poc = float(centres[poc_idx])
    # Expand around POC until value_area_pct of volume is included
    total = hist.sum()
    target = total * value_area_pct
    lo_i, hi_i = poc_idx, poc_idx
    acc = hist[poc_idx]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        left = hist[lo_i - 1] if lo_i > 0 else -1
        right = hist[hi_i + 1] if hi_i < bins - 1 else -1
        if right >= left:
            hi_i += 1
            acc += hist[hi_i]
        else:
            lo_i -= 1
            acc += hist[lo_i]
    return poc, float(centres[hi_i]), float(centres[lo_i])


def _premium_discount(price: float, range_high: float, range_low: float
                     ) -> Tuple[bool, bool]:
    """Premium = upper half of dealing range, Discount = lower half."""
    if range_high <= range_low:
        return False, False
    mid = 0.5 * (range_high + range_low)
    return price > mid, price < mid


# ── Agent ─────────────────────────────────────────────────────────────────────

class OrderFlowAgent(BaseAgent):
    name = "OrderFlowAgent"

    def __init__(self, config=None):
        super().__init__(config)
        self.model = "pure-python"
        self._feed = DataFeed()

    def _default_output(self) -> OrderFlowSignal:
        return OrderFlowSignal(reason="Order flow agent unavailable")

    async def analyze(self, snapshot: Optional[MarketSnapshot] = None, **kw) -> OrderFlowSignal:
        if snapshot is None:
            snapshot = self._feed.get_snapshot()
        df = self._feed.get_ohlcv_dataframe("1h")
        if df.empty or len(df) < 30:
            return OrderFlowSignal(reason="Insufficient 1h OHLCV for order flow")

        price = float(df["close"].iloc[-1])

        # Detections
        obs = _detect_order_blocks(df)
        fvgs = _detect_fvgs(df)
        structure, last_h, last_l = _detect_structure(df)
        sweep = _liquidity_sweep(df)
        poc, vah, val = _volume_profile(df)

        # Premium/discount on the last 50-bar dealing range
        window = df.tail(50)
        range_high = float(window["high"].max())
        range_low = float(window["low"].min())
        in_prem, in_disc = _premium_discount(price, range_high, range_low)

        active_bull_obs = [b for b in obs if b.side == "BULLISH" and not b.mitigated]
        active_bear_obs = [b for b in obs if b.side == "BEARISH" and not b.mitigated]
        active_bull_fvgs = [g for g in fvgs if g.side == "BULLISH" and not g.filled]
        active_bear_fvgs = [g for g in fvgs if g.side == "BEARISH" and not g.filled]

        nearest_demand = max(
            [b.top for b in active_bull_obs] + [g.top for g in active_bull_fvgs] + [val],
            default=val,
        )
        nearest_supply = min(
            [b.bottom for b in active_bear_obs] + [g.bottom for g in active_bear_fvgs] + [vah],
            default=vah,
        )

        # Bias scoring (0..1)
        score_bull, score_bear = 0.0, 0.0
        if structure in ("BOS_UP", "CHOCH_UP"):
            score_bull += 0.30
        if structure in ("BOS_DOWN", "CHOCH_DOWN"):
            score_bear += 0.30
        if sweep == "LOW":
            score_bull += 0.20
        if sweep == "HIGH":
            score_bear += 0.20
        if in_disc:
            score_bull += 0.15
        if in_prem:
            score_bear += 0.15
        if active_bull_obs:
            score_bull += 0.15
        if active_bear_obs:
            score_bear += 0.15
        if active_bull_fvgs:
            score_bull += 0.10
        if active_bear_fvgs:
            score_bear += 0.10
        # POC magnet: price above POC = mild bullish, below = bearish
        if price > poc:
            score_bull += 0.05
        else:
            score_bear += 0.05

        if score_bull > score_bear and score_bull >= 0.45:
            bias = "BULLISH"
            confluence = min(1.0, score_bull)
        elif score_bear > score_bull and score_bear >= 0.45:
            bias = "BEARISH"
            confluence = min(1.0, score_bear)
        else:
            bias = "NEUTRAL"
            confluence = max(score_bull, score_bear)

        reason = (
            f"struct={structure}, sweep={sweep}, "
            f"OBs(B/S)={len(active_bull_obs)}/{len(active_bear_obs)}, "
            f"FVGs(B/S)={len(active_bull_fvgs)}/{len(active_bear_fvgs)}, "
            f"POC={poc:.5f} VAH={vah:.5f} VAL={val:.5f}, "
            f"zone={'PREM' if in_prem else 'DISC' if in_disc else 'MID'}"
        )

        self.logger.info(f"OrderFlow: bias={bias} conf={confluence:.2f} | {reason}")

        return OrderFlowSignal(
            bias=bias,
            confluence_score=round(confluence, 3),
            structure=structure,
            in_premium=in_prem,
            in_discount=in_disc,
            liquidity_swept=sweep,
            poc=round(poc, 5),
            vah=round(vah, 5),
            val=round(val, 5),
            active_order_blocks=len(active_bull_obs) + len(active_bear_obs),
            active_fvgs=len(active_bull_fvgs) + len(active_bear_fvgs),
            nearest_demand=round(nearest_demand, 5),
            nearest_supply=round(nearest_supply, 5),
            reason=reason,
        )
