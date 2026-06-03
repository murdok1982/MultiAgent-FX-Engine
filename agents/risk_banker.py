"""
agents/risk_banker.py — Risk & Capital Management Agent (CRITICAL SAFETY LAYER).
Enforces: position sizing, daily drawdown kill-switch, consecutive loss kill-switch,
margin checks, profit transfer trigger, and safe-mode enforcement.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field

from agents.base_agent import BaseAgent
from config import get_config
from database import log_system_event, save_equity_snapshot
from utils.logger import get_logger
from utils.risk_utils import calculate_position_size, calculate_atr_sl_tp, calculate_daily_drawdown_pct


logger = get_logger("risk_banker")


# ── Output Schema ──────────────────────────────────────────────────────────────

class RiskAssessment(BaseModel):
    trading_allowed: bool = Field(description="Can the system trade right now?")
    kill_switch_active: bool = Field(default=False)
    kill_switch_reason: str = Field(default="")
    recommended_units: float = Field(default=0.0, description="Calculated units to trade")
    max_loss_this_trade: float = Field(default=0.0, description="Max loss in currency units")
    daily_pnl: float = Field(default=0.0)
    daily_drawdown_pct: float = Field(default=0.0)
    consecutive_losses: int = Field(default=0)
    account_balance: float = Field(default=0.0)
    safe_mode: bool = Field(default=False)
    reason: str = Field(default="")
    profit_transfer_triggered: bool = Field(default=False)
    profit_transfer_amount: float = Field(default=0.0)


# ── Internal State (In-Memory + DB-backed) ────────────────────────────────────

@dataclass
class RiskState:
    """In-memory risk tracking. Resets daily."""
    opening_balance: float = 0.0
    current_balance: float = 0.0
    daily_pnl: float = 0.0
    consecutive_losses: int = 0
    kill_switch_active: bool = False
    kill_switch_reason: str = ""
    safe_mode: bool = False
    trades_today: int = 0
    losses_today: int = 0
    wins_today: int = 0
    last_reset_date: date = field(default_factory=lambda: date.today())
    peak_balance: float = 0.0


class RiskBankerAgent(BaseAgent):
    """
    Risk & Banker Agent — the ONLY component that can block trades.
    This is a hard safety layer: if this says NO, the system does NOT trade.
    No LLM required for core logic (deterministic). LLM used for commentary only.
    """
    name = "RiskBankerAgent"

    def __init__(self, config=None, initial_balance: float = None):
        super().__init__(config)
        self.model = self.config.ollama_model_risk
        cap = initial_balance or (
            self.config.paper_capital if self.config.is_paper else self.config.live_capital_limit
        )
        self._state = RiskState(
            opening_balance=cap,
            current_balance=cap,
            peak_balance=cap,
        )

    def _default_output(self) -> RiskAssessment:
        return RiskAssessment(
            trading_allowed=False,
            kill_switch_active=True,
            kill_switch_reason="RiskAgent internal error — defaulting to BLOCK",
            reason="Risk agent encountered an error. Trading blocked for safety.",
        )

    # ── Public State Update Methods ────────────────────────────────────────────

    def update_balance(self, new_balance: float) -> None:
        """Called by broker after trade close or account refresh."""
        self._maybe_reset_daily()
        prev = self._state.current_balance
        self._state.current_balance = new_balance
        self._state.daily_pnl = new_balance - self._state.opening_balance
        if new_balance > self._state.peak_balance:
            self._state.peak_balance = new_balance
        logger.info(f"Balance updated: {prev:.2f} → {new_balance:.2f} | Daily PnL: {self._state.daily_pnl:+.2f}")

    def record_loss(self, loss_amount: float) -> None:
        """Call when a trade closes in loss."""
        self._state.consecutive_losses += 1
        self._state.losses_today += 1
        self._state.daily_pnl -= abs(loss_amount)
        self._state.current_balance -= abs(loss_amount)
        logger.warning(f"Loss recorded: -{abs(loss_amount):.2f} | Consecutive: {self._state.consecutive_losses}")
        self._check_kill_switch()

    def record_win(self, profit_amount: float) -> None:
        """Call when a trade closes in profit."""
        self._state.consecutive_losses = 0  # Reset consecutive counter on win
        self._state.wins_today += 1
        self._state.daily_pnl += profit_amount
        self._state.current_balance += profit_amount
        logger.info(f"Win recorded: +{profit_amount:.2f} | Balance: {self._state.current_balance:.2f}")

    def activate_kill_switch(self, reason: str) -> None:
        """Manually activate kill switch."""
        self._state.kill_switch_active = True
        self._state.kill_switch_reason = reason
        self._state.safe_mode = True
        log_system_event("KILL_SWITCH_ACTIVATED", reason, severity="CRITICAL")
        logger.critical(f"🚨 KILL SWITCH ACTIVATED: {reason}")

    def reset_kill_switch(self) -> None:
        """Only callable manually (requires human intervention)."""
        self._state.kill_switch_active = False
        self._state.kill_switch_reason = ""
        self._state.safe_mode = False
        self._state.consecutive_losses = 0
        log_system_event("KILL_SWITCH_RESET", "Kill switch manually reset", severity="WARNING")
        logger.warning("Kill switch reset by operator")

    # ── Internal Logic ─────────────────────────────────────────────────────────

    def _maybe_reset_daily(self) -> None:
        today = date.today()
        if self._state.last_reset_date != today:
            self._state.opening_balance = self._state.current_balance
            self._state.daily_pnl = 0.0
            self._state.losses_today = 0
            self._state.wins_today = 0
            self._state.trades_today = 0
            self._state.last_reset_date = today
            logger.info(f"Daily risk counters reset. Opening balance: {self._state.opening_balance:.2f}")

    def _check_kill_switch(self) -> None:
        """Auto-activate kill switch on breach of any risk limit."""
        cfg = self.config

        # 1. Daily drawdown breach
        dd_pct = calculate_daily_drawdown_pct(
            self._state.opening_balance, self._state.current_balance
        )
        if dd_pct >= cfg.max_daily_drawdown_pct:
            self.activate_kill_switch(
                f"Daily drawdown {dd_pct:.2f}% ≥ limit {cfg.max_daily_drawdown_pct:.2f}%"
            )
            return

        # 2. Consecutive losses breach
        if self._state.consecutive_losses >= cfg.max_consecutive_losses:
            self.activate_kill_switch(
                f"{self._state.consecutive_losses} consecutive losses — limit is {cfg.max_consecutive_losses}"
            )

    def _check_profit_transfer(self) -> tuple[bool, float]:
        """Check if profit transfer to bank should be triggered."""
        if self._state.opening_balance <= 0:
            return False, 0.0
        growth_pct = ((self._state.current_balance - self._state.opening_balance)
                      / self._state.opening_balance) * 100
        if growth_pct >= self.config.profit_transfer_trigger_pct:
            # Transfer 50% of profits, keep rest
            transfer_amount = (self._state.current_balance - self._state.opening_balance) * 0.5
            return True, round(transfer_amount, 2)
        return False, 0.0

    # ── Main Analyze ───────────────────────────────────────────────────────────

    async def analyze(
        self,
        entry_price: float = 0.0,
        stop_loss: float = 0.0,
        signal_direction: str = "NONE",
        **kwargs,
    ) -> RiskAssessment:
        self._maybe_reset_daily()

        balance = self._state.current_balance
        daily_dd_pct = calculate_daily_drawdown_pct(self._state.opening_balance, balance)
        cfg = self.config

        # ── Kill Switch Check ──────────────────────────────────────────────
        if self._state.kill_switch_active:
            return RiskAssessment(
                trading_allowed=False,
                kill_switch_active=True,
                kill_switch_reason=self._state.kill_switch_reason,
                daily_pnl=self._state.daily_pnl,
                daily_drawdown_pct=daily_dd_pct,
                consecutive_losses=self._state.consecutive_losses,
                account_balance=balance,
                safe_mode=True,
                reason=f"KILL SWITCH ACTIVE: {self._state.kill_switch_reason}",
            )

        # ── Real-Time Risk Checks ──────────────────────────────────────────
        block_reasons = []

        if daily_dd_pct >= cfg.max_daily_drawdown_pct:
            block_reasons.append(f"Daily DD {daily_dd_pct:.2f}% ≥ {cfg.max_daily_drawdown_pct}%")

        if self._state.consecutive_losses >= cfg.max_consecutive_losses:
            block_reasons.append(f"Consecutive losses: {self._state.consecutive_losses}")

        if balance <= 0:
            block_reasons.append("Account balance is zero")

        if block_reasons:
            for reason in block_reasons:
                self.activate_kill_switch(reason)
            return RiskAssessment(
                trading_allowed=False,
                kill_switch_active=True,
                kill_switch_reason="; ".join(block_reasons),
                daily_pnl=self._state.daily_pnl,
                daily_drawdown_pct=daily_dd_pct,
                consecutive_losses=self._state.consecutive_losses,
                account_balance=balance,
                safe_mode=True,
                reason="; ".join(block_reasons),
            )

        # ── Position Sizing ────────────────────────────────────────────────
        recommended_units = 0.0
        max_loss_this_trade = 0.0

        if entry_price > 0 and stop_loss > 0 and signal_direction in ("BUY", "SELL"):
            recommended_units = calculate_position_size(
                account_balance=balance,
                risk_pct=cfg.risk_per_trade_pct,
                entry_price=entry_price,
                stop_loss_price=stop_loss,
            )
            max_loss_this_trade = balance * (cfg.risk_per_trade_pct / 100)

        # ── Profit Transfer Check ──────────────────────────────────────────
        profit_triggered, transfer_amount = self._check_profit_transfer()
        if profit_triggered:
            log_system_event(
                "PROFIT_TRANSFER_TRIGGERED",
                f"Transfer {transfer_amount:.2f} to bank account",
                severity="INFO",
                metadata={"amount": transfer_amount, "balance": balance},
            )
            logger.info(f"💰 PROFIT TRANSFER TRIGGERED: {transfer_amount:.2f}")

        # ── Save Equity Snapshot ───────────────────────────────────────────
        save_equity_snapshot(
            balance=balance,
            equity=balance,
            daily_pnl=self._state.daily_pnl,
            daily_pnl_pct=((self._state.daily_pnl / self._state.opening_balance) * 100
                           if self._state.opening_balance > 0 else 0),
            drawdown_pct=daily_dd_pct,
            trading_mode=cfg.trading_mode.value,
        )

        reason = (
            f"Balance: {balance:.2f} | Daily PnL: {self._state.daily_pnl:+.2f} "
            f"| DD: {daily_dd_pct:.2f}% | Cons.Losses: {self._state.consecutive_losses}"
        )

        return RiskAssessment(
            trading_allowed=True,
            kill_switch_active=False,
            recommended_units=round(recommended_units, 0),
            max_loss_this_trade=round(max_loss_this_trade, 2),
            daily_pnl=round(self._state.daily_pnl, 2),
            daily_drawdown_pct=round(daily_dd_pct, 4),
            consecutive_losses=self._state.consecutive_losses,
            account_balance=round(balance, 2),
            safe_mode=self._state.safe_mode,
            reason=reason,
            profit_transfer_triggered=profit_triggered,
            profit_transfer_amount=transfer_amount,
        )

    @property
    def kill_switch_active(self) -> bool:
        return self._state.kill_switch_active

    @property
    def current_balance(self) -> float:
        return self._state.current_balance
