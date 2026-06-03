"""Trade history and open position endpoints."""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, Query

router = APIRouter()


def _trade_to_dict(t) -> dict:
    return {
        "trade_id": t.trade_id,
        "symbol": t.symbol,
        "side": t.side,
        "units": t.units,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "pnl": t.pnl,
        "pnl_pct": t.pnl_pct,
        "status": t.status,
        "open_time": t.open_time.isoformat() if t.open_time else None,
        "close_time": t.close_time.isoformat() if t.close_time else None,
        "notes": t.notes,
        "broker": t.broker,
        "trading_mode": t.trading_mode,
    }


@router.get("/")
def list_trades(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
    symbol: Optional[str] = None,
):
    """List trades with pagination and optional filters."""
    from database import get_session, Trade
    with get_session() as session:
        q = session.query(Trade)
        if status:
            q = q.filter(Trade.status == status.upper())
        if symbol:
            q = q.filter(Trade.symbol == symbol)
        trades = q.order_by(Trade.open_time.desc()).offset(offset).limit(limit).all()
        return [_trade_to_dict(t) for t in trades]


@router.get("/open")
def get_open_trades():
    """Get all currently open positions."""
    from database import get_session, Trade
    with get_session() as session:
        trades = session.query(Trade).filter(Trade.status == "OPEN").all()
        return [_trade_to_dict(t) for t in trades]


@router.get("/stats")
def get_trade_stats():
    """Aggregate trade statistics for all time."""
    from database import get_session, Trade
    with get_session() as session:
        all_closed = session.query(Trade).filter(Trade.status == "CLOSED").all()
        if not all_closed:
            return {"total_trades": 0}
        pnls = [t.pnl or 0 for t in all_closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        return {
            "total_trades": len(all_closed),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(len(wins) / len(all_closed), 4) if all_closed else 0,
            "total_pnl": round(sum(pnls), 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses else float("inf"),
        }
