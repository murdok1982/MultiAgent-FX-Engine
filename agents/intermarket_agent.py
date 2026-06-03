"""
agents/intermarket_agent.py — Inter-Market Macro Agent
======================================================

Thin wrapper that exposes data/intermarket.py as a BaseAgent so it plugs
into the orchestrator fan-in alongside the other agents.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents.base_agent import BaseAgent
from data.intermarket import get_intermarket_features, InterMarketFeatures


class InterMarketSignal(BaseModel):
    bias: str = Field(default="NEUTRAL", description="BULLISH | BEARISH | NEUTRAL for EURUSD")
    macro_score: float = 0.0
    risk_regime: str = "NEUTRAL"
    dxy_change_1d: float = 0.0
    tnx_change_1d: float = 0.0
    gold_change_1d: float = 0.0
    vix: float = 0.0
    reason: str = ""


class InterMarketAgent(BaseAgent):
    name = "InterMarketAgent"

    def __init__(self, config=None):
        super().__init__(config)
        self.model = "yfinance-features"

    def _default_output(self) -> InterMarketSignal:
        return InterMarketSignal(reason="Intermarket agent unavailable")

    async def analyze(self, **kw: Any) -> InterMarketSignal:
        feats: InterMarketFeatures = get_intermarket_features()
        if not feats.fetched_ok:
            return InterMarketSignal(reason="Macro data unavailable (network/yfinance)")

        score = feats.macro_score
        if score >= 0.2:
            bias = "BULLISH"
        elif score <= -0.2:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"

        reason = (
            f"DXY {feats.dxy_change_1d:+.2f}% (1d), "
            f"10Y {feats.tnx_change_1d:+.2f}%, "
            f"Gold {feats.gold_change_1d:+.2f}%, "
            f"VIX={feats.vix:.1f} ({feats.vix_change_1d:+.1f}%), "
            f"regime={feats.risk_regime}, macro_score={score:+.2f}"
        )
        self.logger.info(f"InterMarket: bias={bias} | {reason}")

        return InterMarketSignal(
            bias=bias,
            macro_score=score,
            risk_regime=feats.risk_regime,
            dxy_change_1d=feats.dxy_change_1d,
            tnx_change_1d=feats.tnx_change_1d,
            gold_change_1d=feats.gold_change_1d,
            vix=feats.vix,
            reason=reason,
        )
