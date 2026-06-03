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

        # Adaptive parameter adjustment based on recent trade history
        recent = self._get_recent_trades_performance(limit=20)
        self._adjust_dynamic_params(recent)

        return result

    def _get_recent_trades_performance(self, limit: int = 20) -> list[dict]:
        """Fetch the last N closed trades from DB."""
        try:
            from database import get_session, Trade
            with get_session() as session:
                trades = (
                    session.query(Trade)
                    .filter(Trade.status == "CLOSED", Trade.pnl.isnot(None))
                    .order_by(Trade.close_time.desc())
                    .limit(limit)
                    .all()
                )
                return [
                    {
                        "pnl": t.pnl or 0.0,
                        "pnl_pct": t.pnl_pct or 0.0,
                        "side": t.side,
                        "symbol": t.symbol,
                    }
                    for t in trades
                ]
        except Exception:
            return []

    def _adjust_dynamic_params(self, recent_trades: list[dict]) -> None:
        """
        Adaptive parameter adjustment based on recent performance.
        Only adjusts if we have >= 10 trades. Changes are small (±0.05 to ±0.15).
        """
        if len(recent_trades) < 10:
            return

        try:
            from database import get_dynamic_param, set_dynamic_param
            from utils.risk_utils import calculate_win_rate, calculate_profit_factor
        except ImportError:
            return

        pnls = [t["pnl"] for t in recent_trades]
        win_rate = calculate_win_rate(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        gross_profit = sum(wins)
        gross_loss = sum(losses)
        profit_factor = calculate_profit_factor(gross_profit, gross_loss)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 1.0

        # Safety bounds — these MUST match _PARAM_BOUNDS in api/routes/params.py
        SL_MIN, SL_MAX   = 0.5,  3.0
        TP_MIN, TP_MAX   = 0.5,  5.0
        CONF_MIN, CONF_MAX = 0.4, 0.95

        # --- SL Multiplier ---
        sl_mult = get_dynamic_param("sl_multiplier", 1.5)
        # If many losses AND losses are large relative to wins → SL might be too tight
        if win_rate < 0.40 and avg_loss > avg_win and sl_mult < 2.5:
            new_sl = round(min(sl_mult + 0.1, min(2.5, SL_MAX)), 2)
            new_sl = max(new_sl, SL_MIN)
            set_dynamic_param("sl_multiplier", new_sl,
                              f"Widening SL: win_rate={win_rate:.2f}, avg_loss={avg_loss:.2f}>avg_win={avg_win:.2f}")
        # If win_rate is high AND profit_factor is good → can tighten SL slightly
        elif win_rate > 0.60 and profit_factor > 1.5 and sl_mult > 1.0:
            new_sl = round(max(sl_mult - 0.05, max(1.0, SL_MIN)), 2)
            set_dynamic_param("sl_multiplier", new_sl,
                              f"Tightening SL: win_rate={win_rate:.2f}, pf={profit_factor:.2f}")

        # --- TP Multiplier ---
        tp_mult = get_dynamic_param("tp_multiplier", 2.5)
        # If win_rate > 60% but profit_factor < 1.2 → wins aren't big enough vs losses
        if win_rate > 0.60 and profit_factor < 1.2 and tp_mult < 4.0:
            new_tp = round(min(tp_mult + 0.1, min(4.0, TP_MAX)), 2)
            new_tp = max(new_tp, TP_MIN)
            set_dynamic_param("tp_multiplier", new_tp,
                              f"Extending TP: win_rate={win_rate:.2f}, pf={profit_factor:.2f}")
        # If profit_factor > 2.0, great — keep or reduce TP for more wins
        elif profit_factor > 2.0 and win_rate < 0.45 and tp_mult > 1.5:
            new_tp = round(max(tp_mult - 0.1, max(1.5, TP_MIN)), 2)
            set_dynamic_param("tp_multiplier", new_tp,
                              f"Reducing TP for more wins: pf={profit_factor:.2f}, win_rate={win_rate:.2f}")

        # --- Min Confidence Threshold ---
        conf_thresh = get_dynamic_param("min_confidence_threshold", 0.65)
        # If win_rate < 35% → signals are poor, raise threshold to be more selective
        if win_rate < 0.35 and conf_thresh < 0.85:
            new_conf = round(min(conf_thresh + 0.02, min(0.85, CONF_MAX)), 3)
            new_conf = max(new_conf, CONF_MIN)
            set_dynamic_param("min_confidence_threshold", new_conf,
                              f"Raising confidence threshold: win_rate={win_rate:.2f}")
        # If win_rate > 65% AND we're trading little → lower threshold slightly
        elif win_rate > 0.65 and conf_thresh > 0.55:
            # Hard floor: never drop below CONF_MIN (0.4) to prevent overtrading
            new_conf = round(max(conf_thresh - 0.01, max(0.55, CONF_MIN)), 3)
            set_dynamic_param("min_confidence_threshold", new_conf,
                              f"Lowering confidence threshold: win_rate={win_rate:.2f}")

        # Log adjustment summary
        try:
            from utils.logger import get_logger
            logger = get_logger(__name__)
            logger.info(
                f"[Critic] Param adjustment: win_rate={win_rate:.2f}, pf={profit_factor:.2f}, "
                f"n_trades={len(recent_trades)}"
            )
        except Exception:
            pass

    def get_performance_summary(self, days: int = 7) -> dict:
        """Returns rolling performance summary as dict."""
        try:
            from database import get_session, Trade, get_all_dynamic_params
            from datetime import datetime, timedelta, timezone
            from utils.risk_utils import calculate_win_rate, calculate_profit_factor, calculate_sharpe_ratio

            since = datetime.now(timezone.utc) - timedelta(days=days)
            with get_session() as session:
                trades = (
                    session.query(Trade)
                    .filter(Trade.status == "CLOSED", Trade.close_time >= since, Trade.pnl.isnot(None))
                    .order_by(Trade.close_time.asc())
                    .all()
                )

            if not trades:
                return {"total_trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
                        "total_pnl": 0.0, "avg_pnl_per_trade": 0.0, "best_trade": 0.0,
                        "worst_trade": 0.0, "current_params": get_all_dynamic_params()}

            pnls = [t.pnl for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [abs(p) for p in pnls if p < 0]

            return {
                "total_trades": len(trades),
                "win_rate": round(calculate_win_rate(pnls), 4),
                "profit_factor": round(calculate_profit_factor(sum(wins), sum(losses)), 4),
                "total_pnl": round(sum(pnls), 2),
                "avg_pnl_per_trade": round(sum(pnls) / len(pnls), 2),
                "best_trade": round(max(pnls), 2),
                "worst_trade": round(min(pnls), 2),
                "sharpe_ratio": round(calculate_sharpe_ratio(pnls), 4),
                "current_params": get_all_dynamic_params(),
            }
        except Exception as e:
            return {"error": str(e), "total_trades": 0}

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
