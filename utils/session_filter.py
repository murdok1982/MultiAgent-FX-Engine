"""Forex session management for EUR/USD trading."""
from __future__ import annotations
from datetime import datetime, timezone, time as dtime
from enum import Enum


class ForexSession(str, Enum):
    ASIAN = "ASIAN"                          # 23:00–08:00 UTC (wide spreads)
    EUROPEAN = "EUROPEAN"                    # 07:00–16:00 UTC
    AMERICAN = "AMERICAN"                    # 13:00–22:00 UTC
    LONDON_NY_OVERLAP = "LONDON_NY_OVERLAP"  # 13:00–17:00 UTC (max liquidity)
    OFF_HOURS = "OFF_HOURS"                  # Weekends / dead zones


def get_current_session(now: datetime | None = None) -> ForexSession:
    """Return the current Forex session based on UTC time."""
    if now is None:
        now = datetime.now(timezone.utc)
    # Weekends: Fri 22:00 UTC to Sun 22:00 UTC → OFF_HOURS
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour = now.hour
    minute = now.minute
    total_minutes = hour * 60 + minute

    if weekday == 5:  # Saturday: always OFF
        return ForexSession.OFF_HOURS
    if weekday == 6 and total_minutes < 22 * 60:  # Sunday before 22:00 UTC
        return ForexSession.OFF_HOURS
    if weekday == 4 and total_minutes >= 22 * 60:  # Friday after 22:00 UTC
        return ForexSession.OFF_HOURS

    # London/NY Overlap: 13:00–17:00 UTC
    if 13 * 60 <= total_minutes < 17 * 60:
        return ForexSession.LONDON_NY_OVERLAP
    # American: 13:00–22:00 UTC (includes overlap, but overlap takes priority above)
    if 13 * 60 <= total_minutes < 22 * 60:
        return ForexSession.AMERICAN
    # European: 07:00–16:00 UTC
    if 7 * 60 <= total_minutes < 16 * 60:
        return ForexSession.EUROPEAN
    # Asian: everything else (23:00–07:00 UTC)
    return ForexSession.ASIAN


def is_high_liquidity_session(now: datetime | None = None) -> bool:
    """True only during London/NY overlap (highest EUR/USD liquidity)."""
    return get_current_session(now) == ForexSession.LONDON_NY_OVERLAP


def is_trading_allowed_by_session(now: datetime | None = None) -> bool:
    """
    True during European and American sessions (including overlap).
    False during Asian session (wide spreads) and OFF_HOURS (weekends).
    """
    session = get_current_session(now)
    return session in (
        ForexSession.EUROPEAN,
        ForexSession.AMERICAN,
        ForexSession.LONDON_NY_OVERLAP,
    )


def get_session_quality_multiplier(now: datetime | None = None) -> float:
    """
    Signal quality multiplier by session:
    - LONDON_NY_OVERLAP: 1.0 (maximum liquidity, tightest spreads)
    - EUROPEAN / AMERICAN only: 0.85 (good but not peak)
    - ASIAN: 0.50 (high spreads, USD/EUR less active)
    - OFF_HOURS: 0.0 (market closed)
    """
    session = get_current_session(now)
    return {
        ForexSession.LONDON_NY_OVERLAP: 1.0,
        ForexSession.EUROPEAN: 0.85,
        ForexSession.AMERICAN: 0.85,
        ForexSession.ASIAN: 0.50,
        ForexSession.OFF_HOURS: 0.0,
    }[session]
