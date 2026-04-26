"""
main.py — Master Trading Loop
Orchestrates all agents in a cyclic execution with watchdog, graceful shutdown,
and DB backup scheduling.

Usage:
    python main.py                   # Paper trading (default)
    python main.py --live            # Live trading (requires confirmation)
    python main.py --interval 5      # Cycle every 5 minutes
    python main.py --dry-run         # Single cycle, no trade execution
"""
from __future__ import annotations

import argparse
import asyncio
import schedule
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

# Force UTF-8 encoding for standard output on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

from rich.console import Console
from rich.prompt import Confirm

# ── Internal imports ───────────────────────────────────────────────────────────
from config import TradingMode, BrokerType, get_config
from database import init_db, backup_database, log_system_event, get_session, Trade
from agents.fundamental import FundamentalAgent
from agents.technical import TechnicalAgent
from agents.risk_banker import RiskBankerAgent
from agents.critic import CriticAgent
from orchestrator import OrchestratorAgent, OrchestratorDecision
from data.data_feed import DataFeed
from execution.broker_interface import BrokerInterface
from execution.paper import PaperBroker
from monitoring.dashboard import Dashboard
from monitoring.alerts import AlertDispatcher
from utils.logger import get_logger

console = Console()
logger = get_logger("main")


from rich.panel import Panel

# ── Risk Warning Banner ────────────────────────────────────────────────────────

RISK_WARNING_TEXT = """
      ⚠️  TRADING SYSTEM — CRITICAL RISK WARNING ⚠️

  Este sistema puede perder dinero real.
  This system can lose real money.

  • Default: PAPER TRADING (simulado, sin riesgo real)
  • LIVE mode requires --live flag + explicit confirmation
  • Past performance does NOT guarantee future results
  • USE UNDER YOUR OWN RESPONSIBILITY
"""
RISK_WARNING_PANEL = Panel(RISK_WARNING_TEXT, border_style="bold yellow", style="bold yellow")


# ── Broker Factory ─────────────────────────────────────────────────────────────

def create_broker(cfg) -> BrokerInterface:
    broker_type = cfg.effective_broker

    if broker_type == BrokerType.PAPER:
        return PaperBroker(initial_balance=cfg.paper_capital)

    elif broker_type == BrokerType.OANDA:
        from execution.oanda import OANDABroker
        if not cfg.oanda_api_key or not cfg.oanda_account_id:
            raise ValueError("OANDA_API_KEY and OANDA_ACCOUNT_ID must be set in .env for live trading")
        return OANDABroker(
            api_key=cfg.oanda_api_key,
            account_id=cfg.oanda_account_id,
            environment=cfg.oanda_environment.value,
        )

    elif broker_type == BrokerType.MT5:
        from execution.mt5 import MT5Broker
        return MT5Broker(
            login=cfg.mt5_login,
            password=cfg.mt5_password or "",
            server=cfg.mt5_server or "",
        )

    else:
        logger.warning(f"Unknown broker type: {broker_type} — falling back to Paper")
        return PaperBroker(initial_balance=cfg.paper_capital)


# ── Main Trading Engine ────────────────────────────────────────────────────────

class TradingEngine:
    """Coordinates the full trading cycle."""

    def __init__(self, broker: BrokerInterface, dry_run: bool = False):
        self.cfg = get_config()
        self.broker = broker
        self.dry_run = dry_run
        self._shutdown = threading.Event()
        self._current_trade_id: Optional[str] = None

        # Initialize agents
        self.data_feed = DataFeed()
        self.fundamental_agent = FundamentalAgent(self.cfg)
        self.technical_agent = TechnicalAgent(self.cfg)
        self.risk_agent = RiskBankerAgent(
            self.cfg,
            initial_balance=self.cfg.paper_capital if self.cfg.is_paper else self.cfg.live_capital_limit
        )
        self.critic_agent = CriticAgent(self.cfg)
        self.orchestrator = OrchestratorAgent(self.cfg, critic=self.critic_agent)
        self.dashboard = Dashboard(refresh_seconds=5.0)
        self.alerts = AlertDispatcher()
        self.last_cycle_ts: Optional[datetime] = None

        logger.info(f"Trading Engine initialized | Mode: {self.cfg.trading_mode.value} | Broker: {broker.broker_name}")

    async def run_cycle(self) -> Optional[OrchestratorDecision]:
        """Execute one complete trading cycle."""
        cycle_start = time.monotonic()
        logger.info("═" * 60)
        logger.info(f"Cycle START @ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}")

        try:
            # ── Step 1: Get market snapshot ──────────────────────────────
            snapshot = self.data_feed.get_snapshot(force_refresh=True)

            # Feed current price to paper broker for SL/TP monitoring
            if isinstance(self.broker, PaperBroker):
                self.broker.update_price(self.cfg.trading_symbol, snapshot.mid)

            # Sync broker balance to risk agent
            try:
                account = self.broker.get_account_info()
                self.risk_agent.update_balance(account.balance)
            except Exception as e:
                logger.warning(f"Could not sync balance: {e}")

            # ── Step 2: Run agents in parallel ───────────────────────────
            fundamental, technical, risk = await asyncio.gather(
                self.fundamental_agent.safe_analyze(),
                self.technical_agent.safe_analyze(snapshot=snapshot),
                self.risk_agent.safe_analyze(
                    entry_price=snapshot.mid,
                    stop_loss=0.0,  # Preliminary check (no signal yet)
                    signal_direction="NONE",
                ),
                return_exceptions=False,
            )

            # ── Step 3: Orchestrator decision ────────────────────────────
            decision = await self.orchestrator.safe_analyze(
                fundamental=fundamental,
                technical=technical,
                risk=risk,
                snapshot=snapshot,
            )

            self.dashboard.update_decision(decision.model_dump())
            self.dashboard.update_kill_switch(self.risk_agent.kill_switch_active)

            logger.info(
                f"Orchestrator → {decision.decision} | "
                f"conf={decision.confidence:.2f} | risk={decision.risk_level}"
            )

            # ── Step 4: Kill-switch check ─────────────────────────────────
            if self.risk_agent.kill_switch_active:
                self.alerts.kill_switch(self.risk_agent._state.kill_switch_reason)
                logger.critical("Kill switch active — no execution this cycle")
                return decision

            # ── Step 5: Execute order (if not dry-run and signal is trade) ──
            if (
                decision.decision in ("BUY", "SELL")
                and not self.dry_run
                and risk.trading_allowed
                and decision.confidence >= self.cfg.min_confidence_threshold
            ):
                await self._execute_trade(decision, snapshot, technical, risk)
            else:
                reason = (
                    "dry-run" if self.dry_run
                    else "HOLD" if decision.decision == "HOLD"
                    else f"confidence {decision.confidence:.2f} < threshold"
                )
                logger.info(f"No execution this cycle: {reason}")

            # ── Step 6: Close positions (monitored by broker/paper) ───────
            await self._monitor_open_positions()

            # ── Step 7: Post-trade analysis ───────────────────────────────
            await self.critic_agent.analyze_pending_trades()

            cycle_time = (time.monotonic() - cycle_start) * 1000
            logger.info(f"Cycle DONE in {cycle_time:.0f}ms")
            return decision

        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)
            log_system_event("CYCLE_ERROR", str(e), severity="WARNING")
            return None

    async def _execute_trade(self, decision, snapshot, technical, risk) -> None:
        """Place a trade order via the broker."""
        side = decision.decision
        sl = technical.suggested_sl if technical else 0.0
        tp = technical.suggested_tp if technical else 0.0
        units = risk.recommended_units if risk.recommended_units > 0 else 1000

        if units < 100:  # Minimum viable trade size
            logger.warning(f"Units too small ({units}) — skipping execution")
            return

        logger.info(f"PLACING ORDER: {side} {units:.0f} {self.cfg.trading_symbol} "
                    f"@ {snapshot.mid:.5f} | SL={sl:.5f} TP={tp:.5f}")

        order = self.broker.place_order(
            symbol=self.cfg.trading_symbol,
            side=side,
            units=units,
            stop_loss=sl if sl > 0 else None,
            take_profit=tp if tp > 0 else None,
        )

        if order.status == "FILLED":
            self._current_trade_id = order.trade_id
            self.alerts.trade_opened(side, self.cfg.trading_symbol, units, order.fill_price)

            # Record in DB
            trade = Trade(
                trade_id=order.trade_id,
                symbol=self.cfg.trading_symbol,
                side=side,
                units=units,
                entry_price=order.fill_price,
                stop_loss=sl,
                take_profit=tp,
                status="OPEN",
                broker=self.broker.broker_name,
                trading_mode=self.cfg.trading_mode.value,
                notes=decision.reasoning,
            )
            with get_session() as s:
                s.add(trade)
                s.commit()
        else:
            logger.error(f"Order rejected: {order.error}")
            log_system_event("ORDER_REJECTED", order.error or "Unknown", severity="WARNING")
            self.risk_agent.record_loss(0)  # Count as order failure

    async def _monitor_open_positions(self) -> None:
        """Check open positions and record any that closed."""
        try:
            positions = self.broker.get_open_positions()
            with get_session() as s:
                open_db_trades = s.query(Trade).filter(Trade.status == "OPEN").all()
                open_ids = {p.trade_id for p in positions}

                for db_trade in open_db_trades:
                    if db_trade.trade_id not in open_ids:
                        # Trade closed (SL/TP hit)
                        close_price = self.data_feed.get_snapshot().mid
                        if db_trade.side == "BUY":
                            pnl = (close_price - db_trade.entry_price) * db_trade.units
                        else:
                            pnl = (db_trade.entry_price - close_price) * db_trade.units
                        pnl_pct = (pnl / (db_trade.entry_price * db_trade.units)) * 100

                        db_trade.status = "CLOSED"
                        db_trade.exit_price = close_price
                        db_trade.pnl = round(pnl, 2)
                        db_trade.pnl_pct = round(pnl_pct, 4)
                        db_trade.close_time = datetime.now(timezone.utc)
                        s.commit()

                        if pnl >= 0:
                            self.risk_agent.record_win(pnl)
                        else:
                            self.risk_agent.record_loss(abs(pnl))

                        self.alerts.trade_closed(db_trade.symbol, pnl, pnl_pct)
                        logger.info(f"Trade closed: {db_trade.trade_id} | PnL={pnl:+.2f}")
        except Exception as e:
            logger.warning(f"Position monitoring error: {e}")

    def shutdown(self) -> None:
        self._shutdown.set()
        logger.info("Trading engine shutdown requested")
        log_system_event("SYSTEM_SHUTDOWN", "Graceful shutdown initiated", severity="INFO")


# ── Main Entry Point ───────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-Agent Forex Trading System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="⚠️  Este sistema puede perder dinero real. Úsalo bajo tu responsabilidad.",
    )
    parser.add_argument("--live", action="store_true", help="Enable LIVE trading (real money)")
    parser.add_argument("--paper", action="store_true", default=True, help="Paper trading mode (default)")
    parser.add_argument("--dry-run", action="store_true", help="Run one analysis cycle without placing orders")
    parser.add_argument("--interval", type=int, default=None, help="Override cycle interval in minutes")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable live dashboard")
    parser.add_argument("--reset-kill-switch", action="store_true", help="Reset kill switch before starting")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    cfg = get_config()

    console.print(RISK_WARNING_PANEL)

    # ── Live Trading Double Confirmation ────────────────────────────────────
    if args.live:
        console.print("\n[bold red]⚠️  LIVE TRADING MODE REQUESTED — REAL MONEY AT RISK[/]\n")
        ok1 = Confirm.ask("[red]Confirm you want to trade with REAL MONEY?[/]")
        if not ok1:
            console.print("Canceled. Running in PAPER mode.")
        else:
            ok2 = Confirm.ask("[red]FINAL CONFIRMATION: All losses are your responsibility?[/]")
            if not ok2:
                console.print("Canceled. Running in PAPER mode.")
            else:
                import os
                os.environ["TRADING_MODE"] = "LIVE"
                from config import reset_config
                reset_config()
                cfg = get_config()
                console.print("[bold red]LIVE MODE ACTIVATED[/]")

    # ── Initialize DB ────────────────────────────────────────────────────────
    console.print("Initializing database...")
    init_db()

    # ── Create Broker ────────────────────────────────────────────────────────
    console.print(f"Creating broker: [cyan]{cfg.effective_broker.value}[/]")
    broker = create_broker(cfg)

    if not broker.is_connected():
        logger.warning("Broker connectivity check failed — continuing anyway (will retry)")

    # ── Create Engine ────────────────────────────────────────────────────────
    engine = TradingEngine(broker=broker, dry_run=args.dry_run)

    if args.reset_kill_switch:
        engine.risk_agent.reset_kill_switch()
        console.print("[yellow]Kill switch reset[/]")

    # ── Start Dashboard ────────────────────────────────────────────────────
    if not args.no_dashboard:
        engine.dashboard.start()

    # ── Start Web API Server ───────────────────────────────────────────────
    if cfg.api_enabled:
        try:
            from api.server import start_api_server
            start_api_server(
                host=cfg.api_host,
                port=cfg.api_port,
                alerts_dispatcher=engine.alerts,
            )
            console.print(
                f"[bold cyan]Web dashboard:[/] http://{cfg.api_host}:{cfg.api_port}"
            )
        except Exception as _api_err:
            logger.warning(f"API server failed to start: {_api_err}")

    # ── DB Backup Scheduler ───────────────────────────────────────────────
    schedule.every(cfg.db_backup_interval_hours).hours.do(backup_database)

    # ── Shutdown Handler ──────────────────────────────────────────────────
    def handle_signal(sig, frame):
        console.print(f"\n[yellow]Signal {sig} received — shutting down gracefully...[/]")
        engine.shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handle_signal)

    # ── Main Loop ─────────────────────────────────────────────────────────
    interval = args.interval or cfg.trading_interval_minutes
    console.print(f"\n[bold green]System started | Interval: {interval}min | Mode: {cfg.trading_mode.value}[/]\n")
    log_system_event("SYSTEM_START", f"Mode={cfg.trading_mode.value} broker={broker.broker_name}")

    CYCLE_TIMEOUT = 300  # 5 minutes — if a cycle hangs longer, it is a system error

    try:
        if args.dry_run:
            console.print("[cyan]DRY RUN — single cycle, no orders placed[/]")
            await engine.run_cycle()
            engine.dashboard.print_snapshot()
            return

        while not engine._shutdown.is_set():
            try:
                async with asyncio.timeout(CYCLE_TIMEOUT):
                    await engine.run_cycle()
                    engine.last_cycle_ts = datetime.now(timezone.utc)
            except TimeoutError:
                logger.critical("TRADING CYCLE TIMED OUT after 300s — possible system hang")
                engine.alerts.critical("Trading cycle timed out — possible system hang")
            schedule.run_pending()
            # Sleep in small chunks so shutdown signal is responsive
            for _ in range(interval * 60 // 5):
                if engine._shutdown.is_set():
                    break
                await asyncio.sleep(5)

    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
        engine.dashboard.stop()
        backup_database()
        console.print("[bold green]Trading system shutdown complete. Final DB backup saved.[/]")


if __name__ == "__main__":
    asyncio.run(main())
