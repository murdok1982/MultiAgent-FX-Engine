"""
agents/critic.py — Post-Trade Critic Agent (Long-Term Memory).
Analyzes closed trades, classifies errors, extracts lessons, and
injects learned context into future Orchestrator decisions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from agents.base_agent import BaseAgent
from config import get_config
from database import get_recent_lessons, save_critic_lesson, get_session, Trade


# ── Output Schema ──────────────────────────────────────────────────────────────

class CriticAnalysis(BaseModel):
    trade_id: str = Field(description="Trade ID being analyzed")
    outcome: str = Field(description="WIN | LOSS | BREAK_EVEN")
    error_type: str = Field(
        description="TECHNICAL | FUNDAMENTAL | EXECUTION | RISK_MANAGEMENT | LLM_BIAS | NONE"
    )
    lesson: str = Field(description="Key lesson learned from this trade")
    suggested_improvement: str = Field(description="Specific actionable improvement")
    confidence: float = Field(default=0.5, description="Critic's confidence in analysis 0-1")


ERROR_TYPES = ("TECHNICAL", "FUNDAMENTAL", "EXECUTION", "RISK_MANAGEMENT", "LLM_BIAS", "NONE")


class CriticAgent(BaseAgent):
    """
    Critic Agent — Analyzes completed trades and builds memory.
    Model: llama3.1:8b
    """
    name = "CriticAgent"

    def __init__(self, config=None):
        super().__init__(config)
        self.model = self.config.ollama_model_critic

    def _default_output(self) -> CriticAnalysis:
        return CriticAnalysis(
            trade_id="unknown",
            outcome="BREAK_EVEN",
            error_type="NONE",
            lesson="Critic agent unavailable — unable to analyze trade",
            suggested_improvement="Ensure Ollama is running for post-trade analysis",
        )

    async def analyze(self, trade: Dict[str, Any] = None, **kwargs) -> CriticAnalysis:
        """
        Analyze a single closed trade and produce a lesson.
        `trade` dict should contain: trade_id, side, entry_price, exit_price, pnl, pnl_pct,
        notes (containing orchestrator reasoning), symbol.
        """
        if trade is None:
            return self._default_output()

        trade_id = trade.get("trade_id", "unknown")
        pnl = trade.get("pnl", 0.0)
        pnl_pct = trade.get("pnl_pct", 0.0)
        side = trade.get("side", "?")
        entry = trade.get("entry_price", 0.0)
        exit_price = trade.get("exit_price", 0.0)
        notes = trade.get("notes", "No decision notes available")

        outcome = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAK_EVEN")

        # Fetch recent memory context
        recent_lessons = get_recent_lessons(limit=5)
        memory_context = "\n".join(f"  - {l}" for l in recent_lessons) or "  No prior lessons."

        prompt = f"""You are a trading performance analyst reviewing a completed Forex trade.

TRADE SUMMARY:
- ID: {trade_id}
- Symbol: {trade.get('symbol', 'EUR_USD')}
- Side: {side}
- Entry: {entry:.5f} | Exit: {exit_price:.5f}
- P&L: {pnl:+.2f} ({pnl_pct:+.2f}%)
- Outcome: {outcome}

DECISION REASONING (from orchestrator):
{notes}

PRIOR LESSONS (recent history):
{memory_context}

TASK:
1. Identify what caused this outcome (if LOSS, what went wrong)
2. Classify the error type: {', '.join(ERROR_TYPES)}
3. Extract ONE specific, actionable lesson
4. Suggest ONE concrete improvement for future trades

Respond with ONLY this JSON:
{{
  "trade_id": "{trade_id}",
  "outcome": "{outcome}",
  "error_type": "<error_type>",
  "lesson": "<specific lesson>",
  "suggested_improvement": "<actionable improvement>",
  "confidence": <0.0-1.0>
}}"""

        result = await self.generate_json(
            prompt=prompt,
            schema_class=CriticAnalysis,
            system=(
                "You are an expert trading performance analyst. "
                "Analyze objectively and respond with ONLY valid JSON. "
                "Be specific and actionable, not generic."
            ),
        )

        if result is None:
            # Fallback rule-based analysis
            if pnl < 0:
                error_type = "TECHNICAL" if abs(pnl_pct) > 1.0 else "RISK_MANAGEMENT"
                lesson = f"Trade {trade_id} lost {abs(pnl):.2f}. Review entry timing."
            else:
                error_type = "NONE"
                lesson = f"Trade {trade_id} profited {pnl:.2f}. Pattern OK."

            result = CriticAnalysis(
                trade_id=trade_id,
                outcome=outcome,
                error_type=error_type,
                lesson=lesson,
                suggested_improvement="Verify LLM availability for detailed post-trade analysis",
                confidence=0.4,
            )

        # Validate error_type
        if result.error_type not in ERROR_TYPES:
            result.error_type = "NONE"

        # Persist lesson to DB
        save_critic_lesson(
            trade_id=trade_id,
            error_type=result.error_type,
            lesson=result.lesson,
            outcome=outcome,
            pnl=pnl,
            market_conditions={"entry": entry, "exit": exit_price, "side": side},
        )

        self.logger.info(
            f"Critic [{trade_id}]: {outcome} | {result.error_type} | {result.lesson[:60]}..."
        )
        return result

    def get_memory_context(self, limit: int = 5) -> str:
        """Get recent lessons formatted for injection into Orchestrator prompt."""
        lessons = get_recent_lessons(limit=limit)
        if not lessons:
            return "No historical lessons available yet. This is an early trading session."
        return "LEARNED FROM PAST TRADES:\n" + "\n".join(f"- {l}" for l in lessons)

    async def analyze_pending_trades(self) -> List[CriticAnalysis]:
        """Analyze all closed trades that haven't been analyzed yet."""
        from database import get_session, Trade, CriticLesson
        results = []
        with get_session() as session:
            analyzed_ids = {
                row.trade_id for row in session.query(CriticLesson.trade_id).all()
            }
            unanalyzed = (
                session.query(Trade)
                .filter(Trade.status == "CLOSED")
                .filter(Trade.trade_id.notin_(analyzed_ids))
                .all()
            )
            for trade in unanalyzed:
                result = await self.safe_analyze(trade=trade.to_dict())
                results.append(result)
        return results
