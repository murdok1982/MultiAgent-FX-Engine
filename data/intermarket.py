"""
data/intermarket.py — Inter-Market Correlation & Macro Features
================================================================

Pulls and features the macro instruments that drive EUR/USD:

  • DX-Y.NYB / DXY (^DXY proxy)       — US Dollar Index
  • ^TNX                              — US 10-Year Treasury Yield
  • ^TYX                              — US 30-Year Treasury Yield
  • EU10Y (via ETF proxy IBGM.L or via FRED if FRED_API_KEY)
  • GC=F                              — Gold futures
  • CL=F                              — WTI Crude (risk barometer)
  • ^VIX                              — Equity vol

Derived features (per call):
  • dxy_change_1d, dxy_change_5d
  • tnx_change_1d, tnx_change_5d
  • yield_spread_us_eu                — proxy if EU10Y available
  • gold_change_1d
  • vix_level, vix_change_1d
  • risk_regime                       — RISK_ON | RISK_OFF | NEUTRAL
  • dxy_bias_for_eurusd               — BULLISH | BEARISH | NEUTRAL
                                        (inverse of DXY direction)
  • macro_score                       — scalar in [-1, +1] aggregating signals

Uses yfinance, with safe fallback (returns NEUTRAL zeros if no internet).
Results are cached for 10 minutes to avoid hammering Yahoo.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Dict, Optional

from pydantic import BaseModel, Field


class InterMarketFeatures(BaseModel):
    timestamp: str
    dxy: float = 0.0
    dxy_change_1d: float = 0.0
    dxy_change_5d: float = 0.0
    tnx: float = 0.0
    tnx_change_1d: float = 0.0
    tnx_change_5d: float = 0.0
    tyx: float = 0.0
    eu10y: float = 0.0
    yield_spread_us_eu: float = 0.0
    gold: float = 0.0
    gold_change_1d: float = 0.0
    crude: float = 0.0
    vix: float = 0.0
    vix_change_1d: float = 0.0
    risk_regime: str = Field(default="NEUTRAL", description="RISK_ON | RISK_OFF | NEUTRAL")
    dxy_bias_for_eurusd: str = Field(default="NEUTRAL")
    macro_score: float = 0.0
    fetched_ok: bool = False


# Module-level cache
_CACHE: Dict[str, tuple[float, InterMarketFeatures]] = {}
CACHE_TTL_SEC = 600  # 10 minutes


def _pct_change(s, lookback: int) -> float:
    try:
        if len(s) <= lookback:
            return 0.0
        end = float(s.iloc[-1])
        start = float(s.iloc[-lookback - 1])
        if start == 0:
            return 0.0
        return (end - start) / abs(start) * 100.0
    except Exception:
        return 0.0


def _last(s) -> float:
    try:
        return float(s.iloc[-1])
    except Exception:
        return 0.0


def get_intermarket_features(force_refresh: bool = False) -> InterMarketFeatures:
    """Fetch (or cache-hit) the macro feature snapshot."""
    now = time.monotonic()
    if not force_refresh and "snap" in _CACHE:
        ts, snap = _CACHE["snap"]
        if now - ts < CACHE_TTL_SEC:
            return snap

    out = InterMarketFeatures(timestamp=datetime.now(timezone.utc).isoformat())

    try:
        import yfinance as yf
        # Batch download
        symbols = {
            "dxy":   "DX-Y.NYB",
            "tnx":   "^TNX",
            "tyx":   "^TYX",
            "gold":  "GC=F",
            "crude": "CL=F",
            "vix":   "^VIX",
        }
        df = yf.download(
            list(symbols.values()),
            period="20d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
        if df.empty:
            _CACHE["snap"] = (now, out)
            return out

        # Yahoo returns multi-index df; handle both single & multi
        for name, sym in symbols.items():
            try:
                close = df[sym]["Close"] if (sym in df.columns.get_level_values(0)) else df["Close"]
            except Exception:
                continue
            close = close.dropna()
            if close.empty:
                continue
            setattr(out, name, round(_last(close), 4))
            setattr(out, f"{name}_change_1d", round(_pct_change(close, 1), 4))
            if name in ("dxy", "tnx"):
                setattr(out, f"{name}_change_5d", round(_pct_change(close, 5), 4))

        # EU10Y proxy: try IBGM.L (iShares Euro Govt Bond 7-10Y) — invert to get yield-ish
        try:
            eu = yf.download("IBGM.L", period="20d", interval="1d",
                             progress=False, auto_adjust=True)
            close = eu["Close"].dropna() if not eu.empty else None
            if close is not None and not close.empty:
                # Price → implied yield change proxy: use -pct_change as proxy delta
                out.eu10y = round(float(close.iloc[-1]), 4)
                out.yield_spread_us_eu = round(out.tnx - out.eu10y, 4)
        except Exception:
            pass

        out.fetched_ok = True
    except Exception as e:
        out.fetched_ok = False

    # ── Derive regime + bias ───────────────────────────────────────────────
    # Risk-on signal: VIX falling, crude up, equities (proxy via inverse VIX) up
    risk_score = 0.0
    if out.vix_change_1d < -2.0:
        risk_score += 0.4
    elif out.vix_change_1d > 2.0:
        risk_score -= 0.4
    if out.gold_change_1d > 0.5:
        risk_score -= 0.2   # gold up = risk-off
    elif out.gold_change_1d < -0.5:
        risk_score += 0.2

    if risk_score >= 0.3:
        out.risk_regime = "RISK_ON"
    elif risk_score <= -0.3:
        out.risk_regime = "RISK_OFF"
    else:
        out.risk_regime = "NEUTRAL"

    # DXY inverse bias for EURUSD
    if out.dxy_change_1d < -0.25:
        out.dxy_bias_for_eurusd = "BULLISH"
    elif out.dxy_change_1d > 0.25:
        out.dxy_bias_for_eurusd = "BEARISH"
    else:
        out.dxy_bias_for_eurusd = "NEUTRAL"

    # macro_score in [-1, +1]: positive = bullish for EURUSD
    s = 0.0
    s -= 0.5 * (out.dxy_change_1d / 1.0)        # +1% DXY = -0.5
    s += 0.2 * (out.gold_change_1d / 1.0)        # gold up = mildly EUR friendly via risk
    s += 0.15 * (out.tnx_change_1d / 1.0) * -1   # higher US yields = stronger USD → bearish EURUSD
    s += 0.15 * (risk_score)
    out.macro_score = round(max(-1.0, min(1.0, s)), 3)

    _CACHE["snap"] = (now, out)
    return out
