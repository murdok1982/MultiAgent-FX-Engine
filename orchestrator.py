"""
orchestrator.py — Chief Trader Agent (llama3.1:8b)
Fuses fundamental, technical, risk, and critic agent outputs into
a single, auditable trading decision with strict JSON output.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent
from agents.critic import CriticAgent
from agents.fundamental import FundamentalSignal
from agents.risk_banker import RiskAssessment
from agents.technical import TechnicalSignal
from config import get_config
from database import log_agent_decision
from utils.security import sanitize_lesson, sanitize_for_prompt


# ── Orchestrator Output Schema ─────────────────────────────────────────────────

class OrchestratorDecision(BaseModel):
    decision: str = Field(description="BUY | SELL | HOLD")
    confidence: float = Field(description="0.0 to 1.0")
    risk_level: str = Field(description="LOW | MEDIUM | HIGH")
    reasoning: str = Field(description="Explanation of decision")
    execution_priority: str = Field(description="IMMEDIATE | WAIT")
    invalidate_conditions: list = Field(default_factory=list)
    cycle_id: str = Field(default="")
    timestamp: str = Field(default="")

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, v: str) -> str:
        v = v.upper()
        return v if v in ("BUY", "SELL", "HOLD") else "HOLD"

    @field_validator("confidence")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        return max(0.0, min(1.0, round(v, 3)))

    @field_validator("risk_level")
    @classmethod
    def validate_risk_level(cls, v: str) -> str:
        v = v.upper()
        return v if v in ("LOW", "MEDIUM", "HIGH") else "MEDIUM"

    @field_validator("execution_priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        v = v.upper()
        return v if v in ("IMMEDIATE", "WAIT") else "WAIT"


def _hold_decision(reason: str, cycle_id: str = "") -> OrchestratorDecision:
    """Factory for safe HOLD decisions."""
    return OrchestratorDecision(
        decision="HOLD",
        confidence=1.0,
        risk_level="HIGH",
        reasoning=reason,
        execution_priority="WAIT",
        invalidate_conditions=["All conditions — Risk layer forced HOLD"],
        cycle_id=cycle_id,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Orchestrator Agent ─────────────────────────────────────────────────────────

class OrchestratorAgent(BaseAgent):
    """
    Chief Trader — fuses all agent signals into a final decision.
    Model: llama3.1:8b
    HARD RULES (pre-LLM, cannot be overridden):
      1. If RiskAssessment.trading_allowed = False → always HOLD
      2. If FundamentalSignal.trading_allowed = False → always HOLD
      3. If confidence < MIN_CONFIDENCE_THRESHOLD → always HOLD
    """
    name = "OrchestratorAgent"

    def __init__(self, config=None, critic: Optional[CriticAgent] = None):
        super().__init__(config)
        self.model = self.config.ollama_model_orchestrator
        self._critic = critic

    def _default_output(self) -> OrchestratorDecision:
        return _hold_decision("Orchestrator internal error — safe HOLD applied")

    async def analyze(
        self,
        fundamental: FundamentalSignal = None,
        technical: TechnicalSignal = None,
        risk: RiskAssessment = None,
        snapshot=None,
        **kwargs,
    ) -> OrchestratorDecision:
        cycle_id = str(uuid.uuid4())[:12]
        cfg = self.config

        # ── HARD RULE 1: Risk Agent Veto ──────────────────────────────────
        if risk is None or not risk.trading_allowed:
            reason = risk.kill_switch_reason if risk else "Risk agent output missing"
            self.logger.warning(f"HOLD (Risk veto): {reason}")
            return _hold_decision(f"Risk Agent VETO: {reason}", cycle_id)

        # ── HARD RULE 2: Fundamental No-Trade Window ───────────────────────
        if fundamental is not None and not fundamental.trading_allowed:
            reason = f"Fundamental no-trade window: {fundamental.reason}"
            self.logger.warning(f"HOLD (Fundamental veto): {reason}")
            return _hold_decision(reason, cycle_id)

        # ── Build Critic Memory Context ───────────────────────────────────
        memory_context = ""
        if self._critic:
            raw_memory = self._critic.get_memory_context(limit=5)
            # Sanitize each lesson line to prevent prompt injection via DB-stored lessons.
            sanitized_lines = [
                sanitize_lesson(line) for line in raw_memory.splitlines()
            ]
            memory_context = "\n".join(sanitized_lines)

        # ── Construct LLM Prompt ──────────────────────────────────────────
        # Sanitize free-text fields that could carry prompt-injection payloads
        # (these come from LLM outputs of sub-agents stored in Pydantic objects).
        fund_str = "UNAVAILABLE" if fundamental is None else (
            f"Sentiment: {fundamental.sentiment} (score: {fundamental.score:+.3f})\n"
            f"  Reason: {sanitize_for_prompt(fundamental.reason, max_len=200)}\n"
            f"  Key events: {', '.join(sanitize_for_prompt(e, max_len=80) for e in fundamental.key_events) if fundamental.key_events else 'none'}"
        )

        tech_str = "UNAVAILABLE" if technical is None else (
            f"Trend: {technical.trend} | Signal: {technical.signal_direction} (strength: {technical.strength:.2f})\n"
            f"  EMA50: {technical.ema_50:.5f} | EMA200: {technical.ema_200:.5f}\n"
            f"  RSI: {technical.rsi:.1f} | MACD_hist: {technical.macd_histogram:.7f}\n"
            f"  ATR: {technical.atr:.6f}\n"
            f"  Reason: {sanitize_for_prompt(technical.reason, max_len=200)}"
        )

        price_str = f"Current price: {snapshot.mid:.5f} | Spread: {snapshot.spread_pips:.1f} pips" if snapshot else "Price unavailable"

        risk_str = (
            f"Balance: {risk.account_balance:.2f} | Daily PnL: {risk.daily_pnl:+.2f} "
            f"| DD: {risk.daily_drawdown_pct:.2f}% | Consecutive losses: {risk.consecutive_losses}\n"
            f"  Recommended units: {risk.recommended_units:.0f} | Max loss: {risk.max_loss_this_trade:.2f}"
        )

        prompt = f"""You are the Chief Trader AI for a professional EUR/USD Forex system.
Your role: synthesize all agent inputs and produce ONE final trading decision.

═══════════════════════════════════════════
MARKET DATA: {price_str}
═══════════════════════════════════════════

FUNDAMENTAL ANALYSIS (Mistral:7b):
{fund_str}

TECHNICAL ANALYSIS (Pure Python indicators):
{tech_str}

RISK / ACCOUNT STATUS (phi3:medium):
{risk_str}

{memory_context}
═══════════════════════════════════════════

DECISION RULES (MANDATORY):
1. NEVER trade against both fundamental AND technical
2. ONLY trade if technical.strength ≥ 0.65 AND fundamental.score not strongly opposing
3. RSI > 75 or < 25 = extra caution required
4. Low confidence = HOLD (better to miss than lose)
5. In doubt = HOLD

Produce exactly this JSON (NO other text):
{{
  "decision": "BUY|SELL|HOLD",
  "confidence": <0.00-1.00>,
  "risk_level": "LOW|MEDIUM|HIGH",
  "reasoning": "<1-2 sentences explaining your decision>",
  "execution_priority": "IMMEDIATE|WAIT",
  "invalidate_conditions": ["<condition that would invalidate this trade>"]
}}"""

        # ── LLM Call ──────────────────────────────────────────────────────
        import time
        start = time.monotonic()
        result = await self.generate_json(
            prompt=prompt,
            schema_class=OrchestratorDecision,
            system=(
                "You are a disciplined, risk-first Chief Trading Officer. "
                "Capital preservation is your top priority. "
                "Respond ONLY with valid JSON. No markdown, no explanations outside JSON."
            ),
        )
        latency_ms = (time.monotonic() - start) * 1000

        # ── Fallback: Rule-Based Decision if LLM fails ────────────────────
        if result is None:
            self.logger.warning("Orchestrator LLM unavailable — using rule-based fallback")
            result = self._rule_based_decision(fundamental, technical, cycle_id)

        # ── HARD RULE 3: Minimum Confidence Gate ─────────────────────────
        if result.decision != "HOLD" and result.confidence < cfg.min_confidence_threshold:
            self.logger.info(
                f"Decision {result.decision} downgraded to HOLD "
                f"(confidence {result.confidence:.2f} < {cfg.min_confidence_threshold:.2f})"
            )
            result = _hold_decision(
                f"Confidence {result.confidence:.2f} below threshold {cfg.min_confidence_threshold:.2f}. "
                f"Original: {result.decision}. Reasoning: {result.reasoning}",
                cycle_id,
            )

        # ── Set cycle metadata ─────────────────────────────────────────────
        result.cycle_id = cycle_id
        result.timestamp = datetime.now(timezone.utc).isoformat()

        # ── Audit to DB ───────────────────────────────────────────────────
        log_agent_decision(
            cycle_id=cycle_id,
            agent_name=self.name,
            model=self.model,
            raw_output=result.model_dump_json(),
            parsed=result.model_dump(),
            confidence=result.confidence,
            latency_ms=latency_ms,
        )

        self.logger.info(
            f"[ORCHESTRATOR] Decision: {result.decision} | "
            f"Confidence: {result.confidence:.2f} | "
            f"Risk: {result.risk_level} | "
            f"Priority: {result.execution_priority}"
        )
        return result

    def _rule_based_decision(
        self,
        fundamental: Optional[FundamentalSignal],
        technical: Optional[TechnicalSignal],
        cycle_id: str,
    ) -> OrchestratorDecision:
        """Pure rule-based fallback when Ollama is unavailable."""
        if technical is None or not technical.entry_signal:
            return _hold_decision("No technical entry signal", cycle_id)

        tech_dir = technical.signal_direction
        fund_score = fundamental.score if fundamental else 0.0

        # Check alignment
        if tech_dir == "BUY" and fund_score >= -0.3:
            decision, confidence = "BUY", min(technical.strength * 0.8, 0.75)
        elif tech_dir == "SELL" and fund_score <= 0.3:
            decision, confidence = "SELL", min(technical.strength * 0.8, 0.75)
        else:
            return _hold_decision("Technical/Fundamental conflict — rule fallback HOLD", cycle_id)

        if confidence < self.config.min_confidence_threshold:
            return _hold_decision(f"Rule-based confidence {confidence:.2f} below threshold", cycle_id)

        return OrchestratorDecision(
            decision=decision,
            confidence=round(confidence, 3),
            risk_level="MEDIUM",
            reasoning=f"Rule-based: Tech={tech_dir}({technical.strength:.2f}), Fund={fund_score:+.2f}",
            execution_priority="WAIT",
            invalidate_conditions=["Trend reversal", "High impact event"],
            cycle_id=cycle_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
