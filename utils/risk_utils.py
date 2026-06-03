"""
utils/risk_utils.py — Pure-math risk and performance calculation functions.
No I/O, no side-effects — fully testable in isolation.
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple


# ── Position Sizing ───────────────────────────────────────────────────────────

def calculate_position_size(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    pip_value: float = 1e-4,
    contract_size: float = 100_000,
) -> float:
    """
    Calculate units to trade given a fixed fractional risk per trade.

    Args:
        account_balance: Current account balance in base currency
        risk_pct: Percentage of account to risk (e.g. 1.0 = 1%)
        entry_price: Planned entry price
        stop_loss_price: Stop-loss price
        pip_value: Value of 1 pip (0.0001 for major pairs)
        contract_size: Lot size (100,000 for standard FX)

    Returns:
        Recommended units (not lots)
    """
    if entry_price <= 0 or stop_loss_price <= 0:
        return 0.0

    risk_amount = account_balance * (risk_pct / 100.0)
    stop_distance = abs(entry_price - stop_loss_price)

    if stop_distance < pip_value:
        return 0.0

    # units = risk_amount / (stop_distance * pip_value_per_unit)
    # For standard FX: 1 unit = 1 currency unit, pip value ≈ pip_value
    # We cap at max 10% of balance in notional exposure
    units = (risk_amount / stop_distance)
    max_units_by_notional = (account_balance * 0.10) / entry_price * contract_size
    return min(units, max_units_by_notional)


def calculate_atr_sl_tp(
    entry_price: float,
    atr: float,
    side: str,
    sl_multiplier: float = 1.5,
    tp_multiplier: float = 2.5,
) -> Tuple[float, float]:
    """
    Calculate stop-loss and take-profit using ATR multiples.

    Args:
        entry_price: Trade entry price
        atr: Current ATR value
        side: 'BUY' or 'SELL'
        sl_multiplier: ATR multiple for stop-loss distance
        tp_multiplier: ATR multiple for take-profit distance

    Returns:
        (stop_loss, take_profit) prices
    """
    sl_distance = atr * sl_multiplier
    tp_distance = atr * tp_multiplier

    if side.upper() == "BUY":
        stop_loss = entry_price - sl_distance
        take_profit = entry_price + tp_distance
    else:
        stop_loss = entry_price + sl_distance
        take_profit = entry_price - tp_distance

    return round(stop_loss, 5), round(take_profit, 5)


# ── Drawdown ──────────────────────────────────────────────────────────────────

def calculate_drawdown(equity_curve: List[float]) -> Tuple[float, float]:
    """
    Calculate maximum drawdown from an equity curve.

    Returns:
        (max_drawdown_abs, max_drawdown_pct) — absolute and percentage
    """
    if len(equity_curve) < 2:
        return 0.0, 0.0

    peak = equity_curve[0]
    max_dd_abs = 0.0
    max_dd_pct = 0.0

    for value in equity_curve:
        if value > peak:
            peak = value
        dd_abs = peak - value
        dd_pct = (dd_abs / peak) * 100 if peak > 0 else 0.0
        if dd_abs > max_dd_abs:
            max_dd_abs = dd_abs
            max_dd_pct = dd_pct

    return round(max_dd_abs, 2), round(max_dd_pct, 4)


def calculate_daily_drawdown_pct(
    opening_balance: float, current_balance: float
) -> float:
    """Daily drawdown as a positive percentage (loss from day open)."""
    if opening_balance <= 0:
        return 0.0
    dd = opening_balance - current_balance
    return round((dd / opening_balance) * 100, 4)


# ── Performance Metrics ───────────────────────────────────────────────────────

def calculate_sharpe_ratio(
    returns: List[float],
    risk_free_rate: float = 0.0,
    periods_per_year: int = 252,
) -> float:
    """
    Annualized Sharpe ratio from a list of period returns (as fractions, not %).

    Returns 0.0 if insufficient data.
    """
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_dev = math.sqrt(variance)

    if std_dev == 0:
        return 0.0

    excess = mean_r - (risk_free_rate / periods_per_year)
    return round((excess / std_dev) * math.sqrt(periods_per_year), 4)


def calculate_profit_factor(
    gross_profit: float, gross_loss: float
) -> float:
    """
    Profit factor = gross_profit / |gross_loss|.
    Returns 0.0 if no losses, infinity-safe.
    """
    if gross_loss == 0:
        return 999.0 if gross_profit > 0 else 0.0
    return round(gross_profit / abs(gross_loss), 4)


def calculate_expectancy(
    win_rate: float,
    avg_win: float,
    avg_loss: float,
) -> float:
    """
    Per-trade statistical expectancy.
    E = (win_rate * avg_win) - (loss_rate * avg_loss)
    """
    loss_rate = 1.0 - win_rate
    return round((win_rate * avg_win) - (loss_rate * abs(avg_loss)), 4)


def calculate_win_rate(pnl_list: List[float]) -> float:
    """Win rate from a list of PnL values."""
    if not pnl_list:
        return 0.0
    wins = sum(1 for p in pnl_list if p > 0)
    return round(wins / len(pnl_list), 4)


def summarize_performance(pnl_list: List[float], equity_curve: List[float]) -> dict:
    """
    Compute all performance metrics from trade PnL list and equity curve.
    Returns a dict ready for logging/display.
    """
    if not pnl_list:
        return {}

    wins = [p for p in pnl_list if p > 0]
    losses = [p for p in pnl_list if p < 0]

    win_rate = calculate_win_rate(pnl_list)
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
    gross_profit = sum(wins)
    gross_loss = sum(losses)

    returns = []
    if len(equity_curve) >= 2:
        for i in range(1, len(equity_curve)):
            if equity_curve[i - 1] > 0:
                returns.append((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])

    _, max_dd_pct = calculate_drawdown(equity_curve)

    return {
        "total_trades": len(pnl_list),
        "win_rate": win_rate,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_pnl": round(gross_profit + gross_loss, 2),
        "profit_factor": calculate_profit_factor(gross_profit, abs(gross_loss)),
        "expectancy": calculate_expectancy(win_rate, avg_win, avg_loss),
        "sharpe_ratio": calculate_sharpe_ratio(returns),
        "max_drawdown_pct": max_dd_pct,
    }
