"""Performance metrics and BacktestResult for the backtesting engine."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    _rich_available = True
except ImportError:
    _rich_available = False

from backtesting.engine import BacktestConfig, BacktestTrade


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: List[BacktestTrade]
    equity_curve: List[float]
    timestamps: List[datetime]

    @property
    def closed_trades(self) -> List[BacktestTrade]:
        return [t for t in self.trades if t.pnl is not None]

    @property
    def total_trades(self) -> int:
        return len(self.closed_trades)

    @property
    def winning_trades(self) -> int:
        return sum(1 for t in self.closed_trades if (t.pnl or 0) > 0)

    @property
    def losing_trades(self) -> int:
        return sum(1 for t in self.closed_trades if (t.pnl or 0) < 0)

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return round(self.winning_trades / self.total_trades, 4)

    @property
    def total_pnl(self) -> float:
        return round(sum(t.pnl or 0 for t in self.closed_trades), 2)

    @property
    def avg_win(self) -> float:
        wins = [t.pnl for t in self.closed_trades if (t.pnl or 0) > 0]
        return round(sum(wins) / len(wins), 2) if wins else 0.0

    @property
    def avg_loss(self) -> float:
        losses = [abs(t.pnl) for t in self.closed_trades if (t.pnl or 0) < 0]
        return round(sum(losses) / len(losses), 2) if losses else 0.0

    @property
    def profit_factor(self) -> float:
        wins = sum(t.pnl for t in self.closed_trades if (t.pnl or 0) > 0)
        losses = sum(abs(t.pnl) for t in self.closed_trades if (t.pnl or 0) < 0)
        return round(wins / losses, 4) if losses > 0 else float('inf')

    @property
    def max_drawdown_pct(self) -> float:
        if len(self.equity_curve) < 2:
            return 0.0
        peak = self.equity_curve[0]
        max_dd = 0.0
        for val in self.equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 4)

    @property
    def sharpe_ratio(self) -> float:
        pnls = [t.pnl or 0 for t in self.closed_trades]
        if len(pnls) < 2:
            return 0.0
        import numpy as np
        returns = np.array(pnls) / self.config.initial_balance
        if returns.std() == 0:
            return 0.0
        return round(float(returns.mean() / returns.std() * (252 ** 0.5)), 4)

    @property
    def expectancy(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return round(self.total_pnl / self.total_trades, 2)

    @property
    def longest_winning_streak(self) -> int:
        streak = max_streak = 0
        for t in self.closed_trades:
            if (t.pnl or 0) > 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        return max_streak

    @property
    def longest_losing_streak(self) -> int:
        streak = max_streak = 0
        for t in self.closed_trades:
            if (t.pnl or 0) < 0:
                streak += 1
                max_streak = max(max_streak, streak)
            else:
                streak = 0
        return max_streak

    def summary(self) -> dict:
        return {
            "symbol": self.config.symbol,
            "period": f"{self.config.start_date} to {self.config.end_date}",
            "initial_balance": self.config.initial_balance,
            "final_balance": round(self.config.initial_balance + self.total_pnl, 2),
            "total_return_pct": round(self.total_pnl / self.config.initial_balance * 100, 2),
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "expectancy_per_trade": self.expectancy,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "longest_win_streak": self.longest_winning_streak,
            "longest_loss_streak": self.longest_losing_streak,
            "sl_multiplier": self.config.sl_multiplier,
            "tp_multiplier": self.config.tp_multiplier,
        }

    def print_report(self) -> None:
        s = self.summary()
        if _rich_available:
            console = Console()
            table = Table(title=f"Backtest Report — {s['symbol']}", box=box.ROUNDED)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="green")
            for k, v in s.items():
                color = "green" if isinstance(v, float) and v > 0 else "red" if isinstance(v, float) and v < 0 else "white"
                table.add_row(k.replace("_", " ").title(), f"[{color}]{v}[/{color}]")
            console.print(table)
        else:
            print("\n" + "=" * 50)
            print(f"BACKTEST REPORT — {s['symbol']}")
            print("=" * 50)
            for k, v in s.items():
                print(f"  {k:<30} {v}")
            print("=" * 50)
