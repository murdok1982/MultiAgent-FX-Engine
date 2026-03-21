"""
database.py — SQLite schema and session management via SQLAlchemy.
All trading decisions, trades, lessons, equity snapshots and system events
are stored here for full auditability.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import get_config


# ── Base ──────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ── Models ────────────────────────────────────────────────────────────────────

class Trade(Base):
    """Full lifecycle of a single trade."""
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), unique=True, index=True)
    symbol = Column(String(16), nullable=False, default="EUR_USD")
    side = Column(String(8), nullable=False)           # BUY | SELL
    units = Column(Float, nullable=False)
    entry_price = Column(Float)
    exit_price = Column(Float)
    stop_loss = Column(Float)
    take_profit = Column(Float)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    status = Column(String(16), default="OPEN")        # OPEN | CLOSED | CANCELLED
    open_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    close_time = Column(DateTime)
    broker = Column(String(16))
    trading_mode = Column(String(8))                   # PAPER | LIVE
    orchestrator_decision_id = Column(Integer)
    notes = Column(Text)

    def to_dict(self) -> Dict[str, Any]:
        return {c.key: getattr(self, c.key) for c in self.__table__.columns}


class AgentDecision(Base):
    """Raw JSON decision from each agent per cycle."""
    __tablename__ = "agent_decisions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cycle_id = Column(String(64), index=True)
    agent_name = Column(String(32), nullable=False)
    model = Column(String(64))
    raw_output = Column(Text)          # raw JSON string
    parsed_decision = Column(Text)     # normalized JSON
    confidence = Column(Float)
    latency_ms = Column(Float)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CriticLesson(Base):
    """Post-trade analysis lesson from the Critic Agent."""
    __tablename__ = "critic_lessons"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trade_id = Column(String(64), index=True)
    error_type = Column(String(32))    # TECHNICAL | FUNDAMENTAL | EXECUTION | RISK_MANAGEMENT | LLM_BIAS
    lesson = Column(Text, nullable=False)
    outcome = Column(String(8))        # WIN | LOSS | BREAK_EVEN
    pnl = Column(Float)
    market_conditions = Column(Text)   # JSON
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    applied_count = Column(Integer, default=0)

    def to_context_string(self) -> str:
        return f"[{self.error_type}] {self.lesson} (PnL: {self.pnl:.2f})"


class EquitySnapshot(Base):
    """Point-in-time equity curve entry."""
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    balance = Column(Float, nullable=False)
    equity = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, default=0.0)
    daily_pnl = Column(Float, default=0.0)
    daily_pnl_pct = Column(Float, default=0.0)
    drawdown_pct = Column(Float, default=0.0)
    open_positions = Column(Integer, default=0)
    trading_mode = Column(String(8))
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class SystemEvent(Base):
    """Audit log for system-level events: kill-switch, errors, backups."""
    __tablename__ = "system_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), default="INFO")      # INFO | WARNING | CRITICAL
    description = Column(Text)
    event_metadata = Column(Text)                      # JSON
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── Engine & Session ─────────────────────────────────────────────────────────

_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        cfg = get_config()
        db_path = Path(cfg.database_path).resolve()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        # Enable WAL mode for concurrent reads
        @event.listens_for(_engine, "connect")
        def set_wal(dbapi_conn, connection_record):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return _engine


def init_db() -> None:
    """Create all tables if they don't exist."""
    Base.metadata.create_all(get_engine())


def get_session() -> Session:
    """Return a new database session."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionFactory()


# ── Helper Functions ─────────────────────────────────────────────────────────

def log_agent_decision(
    cycle_id: str,
    agent_name: str,
    model: str,
    raw_output: str,
    parsed: Dict[str, Any],
    confidence: float,
    latency_ms: float,
) -> None:
    with get_session() as s:
        s.add(AgentDecision(
            cycle_id=cycle_id,
            agent_name=agent_name,
            model=model,
            raw_output=raw_output,
            parsed_decision=json.dumps(parsed),
            confidence=confidence,
            latency_ms=latency_ms,
        ))
        s.commit()


def log_system_event(
    event_type: str,
    description: str,
    severity: str = "INFO",
    metadata: Optional[Dict] = None,
) -> None:
    with get_session() as s:
        s.add(SystemEvent(
            event_type=event_type,
            severity=severity,
            description=description,
            event_metadata=json.dumps(metadata or {}),
        ))
        s.commit()


def save_equity_snapshot(
    balance: float,
    equity: float,
    unrealized_pnl: float = 0.0,
    daily_pnl: float = 0.0,
    daily_pnl_pct: float = 0.0,
    drawdown_pct: float = 0.0,
    open_positions: int = 0,
    trading_mode: str = "PAPER",
) -> None:
    with get_session() as s:
        s.add(EquitySnapshot(
            balance=balance,
            equity=equity,
            unrealized_pnl=unrealized_pnl,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            drawdown_pct=drawdown_pct,
            open_positions=open_positions,
            trading_mode=trading_mode,
        ))
        s.commit()


def get_recent_lessons(limit: int = 10) -> List[str]:
    """Fetch recent Critic lessons as context strings for injection."""
    with get_session() as s:
        lessons = (
            s.query(CriticLesson)
            .order_by(CriticLesson.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [l.to_context_string() for l in reversed(lessons)]


def save_critic_lesson(
    trade_id: str,
    error_type: str,
    lesson: str,
    outcome: str,
    pnl: float,
    market_conditions: Dict,
) -> None:
    with get_session() as s:
        s.add(CriticLesson(
            trade_id=trade_id,
            error_type=error_type,
            lesson=lesson,
            outcome=outcome,
            pnl=pnl,
            market_conditions=json.dumps(market_conditions),
        ))
        s.commit()


def backup_database() -> str:
    """Create a timestamped backup of the SQLite file."""
    cfg = get_config()
    src = Path(cfg.database_path).resolve()
    if not src.exists():
        return "No database to back up"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst = src.parent / f"backups" / f"trading_system_{timestamp}.db"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    log_system_event("DB_BACKUP", f"Database backed up to {dst}", severity="INFO")
    return str(dst)


def get_todays_trades() -> List[Dict]:
    """Return all trades opened today."""
    today = datetime.now(timezone.utc).date()
    with get_session() as s:
        trades = (
            s.query(Trade)
            .filter(Trade.open_time >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc))
            .all()
        )
        return [t.to_dict() for t in trades]
