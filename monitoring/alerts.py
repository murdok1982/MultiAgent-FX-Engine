"""
monitoring/alerts.py — Alert dispatcher with Telegram + console fallback.
"""
from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Set

from utils.logger import get_logger

logger = get_logger("alerts")


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


ICONS = {AlertLevel.INFO: "ℹ️", AlertLevel.WARNING: "⚠️", AlertLevel.CRITICAL: "🚨"}


class AlertDispatcher:
    """Routes alerts to Telegram (optional) and always to console."""

    def __init__(self):
        from config import get_config
        self.cfg = get_config()
        self._tg_bot = None
        self._tg_chat_id = None
        self._ws_connections: Set[Any] = set()
        if self.cfg.telegram_enabled and self.cfg.telegram_token:
            self._init_telegram()

    def _init_telegram(self) -> None:
        try:
            import asyncio
            from telegram import Bot
            self._tg_bot = Bot(token=self.cfg.telegram_token)
            self._tg_chat_id = self.cfg.telegram_chat_id
            logger.info("Telegram alerts enabled")
        except ImportError:
            logger.warning("python-telegram-bot not installed — Telegram alerts disabled")
        except Exception as e:
            logger.warning(f"Telegram init failed: {e}")

    def send(self, message: str, level: AlertLevel = AlertLevel.INFO) -> None:
        icon = ICONS.get(level, "•")
        full_msg = f"{icon} [{level.value}] {message}"
        logger.info(f"ALERT: {full_msg}")
        if self._tg_bot and self._tg_chat_id:
            threading.Thread(
                target=self._send_telegram,
                args=(full_msg,),
                daemon=True,
            ).start()

    def _send_telegram(self, message: str) -> None:
        import asyncio
        try:
            async def _send():
                await self._tg_bot.send_message(
                    chat_id=self._tg_chat_id, text=message, parse_mode="HTML"
                )
            asyncio.run(_send())
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    def info(self, msg: str) -> None:
        self.send(msg, AlertLevel.INFO)

    def warning(self, msg: str) -> None:
        self.send(msg, AlertLevel.WARNING)

    def critical(self, msg: str) -> None:
        self.send(msg, AlertLevel.CRITICAL)

    # ── WebSocket broadcast ───────────────────────────────

    def register_websocket(self, ws: Any) -> None:
        self._ws_connections.add(ws)

    def unregister_websocket(self, ws: Any) -> None:
        self._ws_connections.discard(ws)

    async def broadcast_event(self, event_type: str, data: dict) -> None:
        """Broadcast JSON event to all connected WebSocket clients."""
        if not self._ws_connections:
            return
        message = json.dumps({
            "type": event_type,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        })
        disconnected: Set[Any] = set()
        for ws in list(self._ws_connections):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)
        self._ws_connections -= disconnected

    def _broadcast_sync(self, event_type: str, data: dict) -> None:
        """Fire-and-forget broadcast from sync context."""
        if not self._ws_connections:
            return
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.broadcast_event(event_type, data))
        except RuntimeError:
            pass

    # ── High-level helpers ────────────────────────────────

    def kill_switch(self, reason: str) -> None:
        self.critical(f"KILL SWITCH ACTIVATED: {reason}")
        self._broadcast_sync("kill_switch", {"reason": reason})

    def trade_opened(self, side: str, symbol: str, units: float, price: float) -> None:
        self.info(f"{side} {units:.0f} {symbol} @ {price:.5f}")
        self._broadcast_sync("trade_opened", {
            "side": side, "symbol": symbol, "units": units, "price": price
        })

    def trade_closed(self, symbol: str, pnl: float, pnl_pct: float) -> None:
        emoji = "✅" if pnl >= 0 else "❌"
        self.info(f"{emoji} Closed {symbol}: P&L = {pnl:+.2f} ({pnl_pct:+.2f}%)")
        self._broadcast_sync("trade_closed", {
            "symbol": symbol, "pnl": pnl, "pnl_pct": pnl_pct
        })
