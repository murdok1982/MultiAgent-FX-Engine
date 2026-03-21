"""
monitoring/dashboard.py — Rich CLI dashboard for real-time system monitoring.
Displays: equity, trades, agent decisions, kill-switch status, and metrics.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from config import get_config
from database import get_session, EquitySnapshot, Trade, SystemEvent, AgentDecision
from utils.risk_utils import summarize_performance

console = Console()


def _colored_pnl(pnl: float) -> Text:
    s = f"{pnl:+.2f}"
    return Text(s, style="green bold" if pnl >= 0 else "red bold")


def _make_header(mode: str, kill_switch: bool) -> Panel:
    mode_style = "red bold" if mode == "LIVE" else "cyan bold"
    ks_text = " 🚨 KILL SWITCH ACTIVE" if kill_switch else ""
    title = Text()
    title.append("🤖 Multi-Agent Forex Trader | ", style="white")
    title.append(f" {mode} MODE ", style=f"on {'red' if mode == 'LIVE' else 'dark_cyan'}")
    title.append(ks_text, style="red bold blink")
    return Panel(title, style="bold blue")


def _make_equity_panel(equity_data: Optional[dict]) -> Panel:
    if not equity_data:
        return Panel("No equity data yet", title="💰 Equity")

    balance = equity_data.get("balance", 0)
    daily_pnl = equity_data.get("daily_pnl", 0)
    dd_pct = equity_data.get("drawdown_pct", 0)
    open_pos = equity_data.get("open_positions", 0)

    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    grid.add_row(
        Text(f"Balance: ${balance:,.2f}", style="white bold"),
        Text(f"Daily PnL: ", style="white") + _colored_pnl(daily_pnl),
    )
    grid.add_row(
        Text(f"Drawdown: {dd_pct:.2f}%", style="yellow" if dd_pct > 1 else "green"),
        Text(f"Open Positions: {open_pos}", style="cyan"),
    )
    return Panel(grid, title="💰 Account", border_style="green" if daily_pnl >= 0 else "red")


def _make_recent_trades(trades: List[dict]) -> Panel:
    table = Table(box=box.SIMPLE, show_header=True, header_style="bold magenta")
    table.add_column("ID", style="dim", width=14)
    table.add_column("Side", width=6)
    table.add_column("Entry", width=8)
    table.add_column("Exit", width=8)
    table.add_column("PnL", width=10)
    table.add_column("Status", width=10)

    for t in trades[:8]:
        side_style = "green" if t.get("side") == "BUY" else "red"
        pnl = t.get("pnl") or 0
        pnl_text = _colored_pnl(pnl)
        table.add_row(
            str(t.get("trade_id", ""))[:13],
            Text(t.get("side", "?"), style=side_style),
            f"{t.get('entry_price', 0):.5f}",
            f"{t.get('exit_price', 0):.5f}" if t.get("exit_price") else "—",
            pnl_text,
            t.get("status", "?"),
        )

    return Panel(table, title="📋 Recent Trades", border_style="blue")


def _make_last_decision(decision: Optional[dict]) -> Panel:
    if not decision:
        return Panel("No decision yet — waiting for first cycle", title="🧠 Last Decision")

    d = decision.get("decision", "HOLD")
    conf = decision.get("confidence", 0)
    risk = decision.get("risk_level", "?")
    reasoning = decision.get("reasoning", "—")[:100]

    color = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(d, "white")
    text = Text()
    text.append(f"  Decision: ", style="dim")
    text.append(f"{d}  ", style=f"{color} bold")
    text.append(f"Confidence: {conf:.0%}  ", style="white")
    text.append(f"Risk: {risk}\n", style="yellow")
    text.append(f"  Reason: {reasoning}", style="dim")

    return Panel(text, title="🧠 Orchestrator Last Decision", border_style=color)


def _make_metrics(pnl_list: List[float], equity_curve: List[float]) -> Panel:
    if len(pnl_list) < 2:
        return Panel("Insufficient data for metrics", title="📊 Metrics")

    metrics = summarize_performance(pnl_list, equity_curve)
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_row(
        f"Win Rate: {metrics.get('win_rate', 0):.0%}",
        f"Sharpe: {metrics.get('sharpe_ratio', 0):.2f}",
        f"Profit Factor: {metrics.get('profit_factor', 0):.2f}",
    )
    table.add_row(
        f"Expectancy: ${metrics.get('expectancy', 0):.2f}",
        f"Max DD: {metrics.get('max_drawdown_pct', 0):.2f}%",
        f"Total Trades: {metrics.get('total_trades', 0)}",
    )
    return Panel(table, title="📊 Performance Metrics", border_style="cyan")


# ── Main Dashboard Class ───────────────────────────────────────────────────────

class Dashboard:
    """
    Live Rich CLI dashboard. Runs in a background thread.
    Updates every `refresh_seconds` seconds.
    """

    def __init__(self, refresh_seconds: float = 5.0):
        self.cfg = get_config()
        self.refresh_seconds = refresh_seconds
        self._last_decision: Optional[dict] = None
        self._kill_switch: bool = False
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def update_decision(self, decision: dict) -> None:
        self._last_decision = decision

    def update_kill_switch(self, active: bool) -> None:
        self._kill_switch = active

    def _fetch_data(self) -> dict:
        """Fetch current state from DB."""
        with get_session() as s:
            latest_equity = (
                s.query(EquitySnapshot)
                .order_by(EquitySnapshot.timestamp.desc())
                .first()
            )
            recent_trades = (
                s.query(Trade)
                .order_by(Trade.open_time.desc())
                .limit(8)
                .all()
            )
            pnl_list = [t.pnl for t in s.query(Trade).filter(Trade.pnl.isnot(None)).all()]
            equity_curve = [
                e.equity for e in s.query(EquitySnapshot).order_by(EquitySnapshot.timestamp).all()
            ]

        return {
            "equity": latest_equity.__dict__ if latest_equity else {},
            "trades": [t.to_dict() for t in recent_trades],
            "pnl_list": pnl_list,
            "equity_curve": equity_curve,
        }

    def _render(self) -> Layout:
        try:
            data = self._fetch_data()
        except Exception:
            data = {"equity": {}, "trades": [], "pnl_list": [], "equity_curve": []}

        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=6),
        )
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        mode = self.cfg.trading_mode.value
        layout["header"].update(_make_header(mode, self._kill_switch))
        layout["left"].update(_make_equity_panel(data["equity"]))
        layout["right"].update(_make_last_decision(self._last_decision))
        layout["footer"].split_row(
            Layout(_make_recent_trades(data["trades"])),
            Layout(_make_metrics(data["pnl_list"], data["equity_curve"])),
        )
        return layout

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        with Live(self._render(), console=console, refresh_per_second=0.5, screen=True) as live:
            while self._running:
                try:
                    live.update(self._render())
                except Exception:
                    pass
                time.sleep(self.refresh_seconds)

    def stop(self) -> None:
        self._running = False

    def print_snapshot(self) -> None:
        """Print a single dashboard snapshot (non-Live mode)."""
        console.print(self._render())
