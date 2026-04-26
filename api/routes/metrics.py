"""Performance metrics endpoints."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/performance")
def get_performance(days: int = Query(30, ge=1, le=365)):
    """Rolling performance stats for the last N days."""
    from database import get_session, Trade
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as session:
        trades = (
            session.query(Trade)
            .filter(Trade.status == "CLOSED", Trade.close_time >= since)
            .order_by(Trade.close_time.desc())
            .all()
        )
    if not trades:
        return {"total_trades": 0, "period_days": days}
    pnls = [t.pnl or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [abs(p) for p in pnls if p < 0]
    return {
        "period_days": days,
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(trades), 4),
        "total_pnl": round(sum(pnls), 2),
        "profit_factor": round(sum(wins) / sum(losses), 4) if losses else float("inf"),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
    }


@router.get("/equity-curve")
def get_equity_curve(days: int = Query(30, ge=1, le=365)):
    """Equity curve data points for charting."""
    from database import get_session, EquitySnapshot
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as session:
        snapshots = (
            session.query(EquitySnapshot)
            .filter(EquitySnapshot.timestamp >= since)
            .order_by(EquitySnapshot.timestamp.asc())
            .all()
        )
    return [
        {
            "ts": s.timestamp.isoformat(),
            "balance": s.balance,
            "equity": s.equity,
            "daily_pnl": s.daily_pnl,
            "drawdown_pct": s.drawdown_pct,
        }
        for s in snapshots
    ]


@router.get("/daily-pnl")
def get_daily_pnl(days: int = Query(30, ge=1, le=365)):
    """Daily P&L aggregated by date."""
    from database import get_session, Trade
    since = datetime.now(timezone.utc) - timedelta(days=days)
    with get_session() as session:
        trades = (
            session.query(Trade)
            .filter(Trade.status == "CLOSED", Trade.close_time >= since, Trade.pnl.isnot(None))
            .order_by(Trade.close_time.asc())
            .all()
        )
    daily: dict[str, float] = {}
    for t in trades:
        day = t.close_time.date().isoformat() if t.close_time else "unknown"
        daily[day] = round(daily.get(day, 0.0) + (t.pnl or 0), 2)
    return [{"date": d, "pnl": p} for d, p in sorted(daily.items())]
