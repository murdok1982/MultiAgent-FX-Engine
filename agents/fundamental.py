"""
agents/fundamental.py — Fundamental Analysis Agent (Mistral:7b)
Analyses news sentiment and economic calendar.
Enforces NO-TRADE window around HIGH IMPACT events.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from config import get_config
from data.news_fetcher import NewsAnalysis, NewsFetcher


# ── Output Schema ──────────────────────────────────────────────────────────────

class FundamentalSignal(BaseModel):
    sentiment: str = Field(description="BULLISH | BEARISH | NEUTRAL")
    score: float = Field(description="Sentiment score from -1.0 (bearish) to +1.0 (bullish)")
    high_impact_event: bool = Field(default=False, description="Is there a HIGH IMPACT event active?")
    trading_allowed: bool = Field(default=True, description="Is trading allowed right now?")
    reason: str = Field(description="Brief explanation of fundamental view")
    key_events: list = Field(default_factory=list, description="List of key events noted")

    @field_validator("score")
    @classmethod
    def clamp_score(cls, v: float) -> float:
        return max(-1.0, min(1.0, round(v, 3)))

    @field_validator("sentiment")
    @classmethod
    def validate_sentiment(cls, v: str) -> str:
        v = v.upper()
        if v not in ("BULLISH", "BEARISH", "NEUTRAL"):
            return "NEUTRAL"
        return v


# ── Agent ─────────────────────────────────────────────────────────────────────

class FundamentalAgent(BaseAgent):
    name = "FundamentalAgent"

    def __init__(self, config=None):
        super().__init__(config)
        self.model = self.config.ollama_model_fundamental
        self._fetcher = NewsFetcher()

    def _default_output(self) -> FundamentalSignal:
        return FundamentalSignal(
            sentiment="NEUTRAL",
            score=0.0,
            high_impact_event=False,
            trading_allowed=True,
            reason="Fundamental agent unavailable — defaulting to NEUTRAL",
        )

    async def analyze(self, **kwargs) -> FundamentalSignal:
        """
        Fetch real news + calendar, score sentiment, enforce no-trade window.
        Uses Mistral:7b to interpret and produce structured output.
        """
        # 1. Gather data
        news_analysis: NewsAnalysis = self._fetcher.analyze()

        # 2. Hard rule — NO TRADE during HIGH IMPACT window
        if news_analysis.no_trade_window:
            self.logger.warning(
                f"NO-TRADE WINDOW ACTIVE: {news_analysis.no_trade_reason}"
            )
            return FundamentalSignal(
                sentiment="NEUTRAL",
                score=0.0,
                high_impact_event=True,
                trading_allowed=False,
                reason=f"High-impact event window: {news_analysis.no_trade_reason}",
                key_events=[news_analysis.no_trade_reason],
            )

        # 3. Build prompt for Mistral
        recent_headlines = "\n".join([
            f"- [{n.source}] {n.title} (sentiment: {n.sentiment_score:+.2f})"
            for n in news_analysis.news_items[:8]
        ]) or "No recent news available."

        upcoming_events = "\n".join([
            f"- {e.name} at {e.datetime_utc.strftime('%H:%M UTC')} ({e.currency}, {e.impact} impact)"
            for e in news_analysis.calendar_events
        ]) or "No high-impact events in next 24h."

        prompt = f"""You are a fundamental FX analyst specializing in EUR/USD.

CURRENT MARKET CONDITIONS:
- Pre-calculated sentiment score: {news_analysis.sentiment_score:+.3f}
- High impact event active: {news_analysis.has_high_impact_event}
- Description: {news_analysis.high_impact_description}

RECENT NEWS HEADLINES:
{recent_headlines}

UPCOMING HIGH-IMPACT CALENDAR EVENTS (next 24h):
{upcoming_events}

Based on this fundamental data, produce a JSON analysis of EUR/USD outlook.
Consider: ECB vs Fed divergence, risk sentiment, macro environment.

Return ONLY this JSON (no other text):
{{
  "sentiment": "BULLISH|BEARISH|NEUTRAL",
  "score": <float -1.0 to 1.0>,
  "high_impact_event": <true|false>,
  "trading_allowed": <true|false>,
  "reason": "<brief explanation>",
  "key_events": ["<event1>", "<event2>"]
}}"""

        # 4. Call LLM
        result = await self.generate_json(
            prompt=prompt,
            schema_class=FundamentalSignal,
            system=(
                "You are a quantitative fundamental analyst. "
                "You MUST respond with ONLY valid JSON, no other text. "
                "Be concise. Score range is -1.0 to +1.0."
            ),
        )

        # 5. Fallback to pure-keyword analysis if LLM fails
        if result is None:
            self.logger.warning("LLM unavailable — using keyword-only fundamental analysis")
            score = news_analysis.sentiment_score
            sentiment = "BULLISH" if score > 0.1 else ("BEARISH" if score < -0.1 else "NEUTRAL")
            result = FundamentalSignal(
                sentiment=sentiment,
                score=score,
                high_impact_event=news_analysis.has_high_impact_event,
                trading_allowed=not news_analysis.no_trade_window,
                reason=f"Keyword analysis: score={score:+.3f}",
                key_events=[e.name for e in news_analysis.calendar_events[:3]],
            )

        self.logger.info(
            f"Fundamental: {result.sentiment} score={result.score:+.3f} "
            f"tradeable={result.trading_allowed}"
        )
        return result
