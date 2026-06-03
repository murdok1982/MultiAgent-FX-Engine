"""System status and control endpoints."""
from __future__ import annotations
import os
from fastapi import APIRouter, HTTPException, Header
from typing import Annotated, Optional

router = APIRouter()


def _require_operator_token(x_operator_token: Optional[str]) -> None:
    """
    Guard for destructive control endpoints (kill-switch reset, etc.).

    The caller must supply the HTTP header  X-Operator-Token  whose value
    matches the OPERATOR_TOKEN environment variable.  If that variable is
    not set the endpoint is unconditionally blocked — this forces explicit
    configuration before any operator action can be taken via the API.
    """
    expected = os.environ.get("OPERATOR_TOKEN", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=(
                "Kill-switch reset is disabled: OPERATOR_TOKEN env var is not configured. "
                "Set it in your .env file before enabling remote reset."
            ),
        )
    if not x_operator_token or x_operator_token != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Operator-Token header.",
        )


@router.get("/status")
def get_status():
    """Current system status."""
    from database import get_session, SystemEvent, Trade, EquitySnapshot
    with get_session() as session:
        last_snapshot = (
            session.query(EquitySnapshot)
            .order_by(EquitySnapshot.timestamp.desc())
            .first()
        )
        open_count = session.query(Trade).filter(Trade.status == "OPEN").count()
        last_event = (
            session.query(SystemEvent)
            .filter(SystemEvent.event_type == "KILL_SWITCH_ACTIVATED")
            .order_by(SystemEvent.timestamp.desc())
            .first()
        )
        reset_event = (
            session.query(SystemEvent)
            .filter(SystemEvent.event_type == "KILL_SWITCH_RESET")
            .order_by(SystemEvent.timestamp.desc())
            .first()
        )

    kill_switch_active = False
    if last_event:
        kill_switch_active = not (
            reset_event and reset_event.timestamp > last_event.timestamp
        )

    return {
        "balance": last_snapshot.balance if last_snapshot else None,
        "equity": last_snapshot.equity if last_snapshot else None,
        "daily_pnl": last_snapshot.daily_pnl if last_snapshot else None,
        "drawdown_pct": last_snapshot.drawdown_pct if last_snapshot else None,
        "open_positions": open_count,
        "kill_switch_active": kill_switch_active,
        "last_snapshot_ts": last_snapshot.timestamp.isoformat() if last_snapshot else None,
    }


@router.get("/events")
def get_recent_events(limit: int = 50):
    """Recent system events."""
    from database import get_session, SystemEvent
    with get_session() as session:
        events = (
            session.query(SystemEvent)
            .order_by(SystemEvent.timestamp.desc())
            .limit(limit)
            .all()
        )
    return [
        {
            "event_type": e.event_type,
            "severity": e.severity,
            "description": e.description,
            "timestamp": e.timestamp.isoformat(),
        }
        for e in events
    ]


@router.get("/lessons")
def get_recent_lessons(limit: int = 50):
    """Recent critic lessons."""
    from database import get_session, CriticLesson
    with get_session() as session:
        lessons = (
            session.query(CriticLesson)
            .order_by(CriticLesson.timestamp.desc())
            .limit(min(limit, 200))   # hard cap — prevent memory exhaustion
            .all()
        )
    return [
        {
            "trade_id": l.trade_id,
            "error_type": l.error_type,
            "lesson": l.lesson,
            "outcome": l.outcome,
            "pnl": l.pnl,
            "timestamp": l.timestamp.isoformat(),
        }
        for l in lessons
    ]


@router.post("/kill-switch/reset")
def reset_kill_switch(x_operator_token: Annotated[Optional[str], Header()] = None):
    """
    Reset the kill-switch flag in the audit database.

    This endpoint only marks the event in the DB — the in-memory
    RiskBankerAgent state is authoritative and must be reset via the
    --reset-kill-switch CLI flag or operator restart.

    Requires the X-Operator-Token header to match the OPERATOR_TOKEN
    environment variable.  The endpoint is blocked (503) when that
    variable is not configured.
    """
    _require_operator_token(x_operator_token)

    from database import log_system_event
    log_system_event(
        "KILL_SWITCH_RESET",
        "Kill switch reset via API by authenticated operator",
        severity="WARNING",
    )
    return {
        "status": "recorded",
        "note": (
            "DB event recorded. Restart the trading engine with --reset-kill-switch "
            "to clear the in-memory kill-switch state."
        ),
    }
