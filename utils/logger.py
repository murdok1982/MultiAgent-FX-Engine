"""
utils/logger.py — Structured, immutable, rotating logger.
Outputs JSON to file and rich-formatted text to console.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.logging import RichHandler

from config import get_config

_loggers: Dict[str, logging.Logger] = {}
_console = Console(stderr=True)


def _json_formatter(record: logging.LogRecord) -> str:
    payload: Dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "msg": record.getMessage(),
    }
    if record.exc_info:
        payload["exc"] = logging.Formatter().formatException(record.exc_info)
    if hasattr(record, "extra"):
        payload.update(record.extra)
    return json.dumps(payload, ensure_ascii=False)


class _JsonFileHandler(logging.FileHandler):
    """Appends one JSON object per line (NDJSON / JSON-lines format)."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = _json_formatter(record)
            self.stream.write(msg + "\n")
            self.flush()
        except Exception:
            self.handleError(record)


def get_logger(name: str = "trading") -> logging.Logger:
    """Return (or create) a named logger with file + console handlers."""
    if name in _loggers:
        return _loggers[name]

    cfg = get_config()
    log_dir = Path(cfg.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, cfg.log_level.value, logging.INFO))
    logger.propagate = False

    # ── JSON file handler (immutable append-only) ──────────────────────────
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_handler = _JsonFileHandler(
        log_dir / f"trading_{date_str}.log", mode="a", encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # ── Rich console handler ───────────────────────────────────────────────
    console_handler = RichHandler(
        console=_console,
        show_time=True,
        show_level=True,
        show_path=False,
        rich_tracebacks=True,
        markup=True,
    )
    console_handler.setLevel(getattr(logging, cfg.log_level.value, logging.INFO))
    logger.addHandler(console_handler)

    _loggers[name] = logger
    return logger


def log_trade_event(
    logger: logging.Logger,
    event: str,
    trade_id: Optional[str] = None,
    **kwargs: Any,
) -> None:
    """Convenience: structured trade event log entry."""
    logger.info(
        f"[TRADE] {event}",
        extra={"trade_id": trade_id, "event": event, **kwargs},
    )


def log_agent_event(
    logger: logging.Logger,
    agent: str,
    decision: str,
    confidence: float = 0.0,
    **kwargs: Any,
) -> None:
    """Convenience: structured agent decision log entry."""
    logger.info(
        f"[AGENT:{agent}] {decision} (conf={confidence:.2f})",
        extra={"agent": agent, "decision": decision, "confidence": confidence, **kwargs},
    )
