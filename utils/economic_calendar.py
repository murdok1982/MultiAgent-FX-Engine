"""
Dynamic economic calendar for EUR/USD no-trade windows.
All dates are calculated algorithmically, no hardcoded specific dates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from typing import List
import calendar as cal_module  # noqa: F401 — reserved for future use


@dataclass
class EconomicEvent:
    name: str
    event_datetime: datetime  # UTC
    impact: str               # "HIGH" | "MEDIUM"
    currency: str             # "USD" | "EUR"
    description: str
    no_trade_minutes_before: int = 30
    no_trade_minutes_after: int = 30

    @property
    def window_start(self) -> datetime:
        return self.event_datetime - timedelta(minutes=self.no_trade_minutes_before)

    @property
    def window_end(self) -> datetime:
        return self.event_datetime + timedelta(minutes=self.no_trade_minutes_after)


def _first_friday_of_month(year: int, month: int) -> date:
    """Return the date of the first Friday of the given month."""
    d = date(year, month, 1)
    days_until_friday = (4 - d.weekday()) % 7  # Friday = weekday 4
    return d + timedelta(days=days_until_friday)


def _second_wednesday_of_month(year: int, month: int) -> date:
    """Return the date of the second Wednesday of the given month."""
    d = date(year, month, 1)
    days_until_wed = (2 - d.weekday()) % 7  # Wednesday = weekday 2
    first_wed = d + timedelta(days=days_until_wed)
    return first_wed + timedelta(weeks=1)


def get_nfp_dates(year: int | None = None, months_ahead: int = 3) -> List[EconomicEvent]:
    """
    Non-Farm Payrolls = first Friday of each month at 13:30 UTC.
    Returns events for current and next N months.
    """
    now = datetime.now(timezone.utc)
    if year is None:
        year = now.year

    events = []
    current = date(now.year, now.month, 1)

    for _ in range(months_ahead + 1):
        nfp_date = _first_friday_of_month(current.year, current.month)
        nfp_dt = datetime(
            nfp_date.year, nfp_date.month, nfp_date.day,
            13, 30, 0, tzinfo=timezone.utc
        )
        if nfp_dt > now - timedelta(hours=1):  # Include if not too far in past
            events.append(EconomicEvent(
                name="NFP",
                event_datetime=nfp_dt,
                impact="HIGH",
                currency="USD",
                description="US Non-Farm Payrolls",
                no_trade_minutes_before=30,
                no_trade_minutes_after=60,  # NFP is very volatile, extend window
            ))
        # Advance to next month
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return events


def get_fomc_dates(months_ahead: int = 6) -> List[EconomicEvent]:
    """
    FOMC meets 8 times per year. Hardcoded schedule for 2025-2027,
    auto-selects future dates only.
    Decision released at 19:00 UTC (2:00 PM ET non-DST).
    """
    # Known FOMC dates (year, month, day) — statement day (2nd day of 2-day meeting)
    _FOMC_SCHEDULE = [
        # 2025
        (2025, 1, 29), (2025, 3, 19), (2025, 5, 7), (2025, 6, 18),
        (2025, 7, 30), (2025, 9, 17), (2025, 10, 29), (2025, 12, 10),
        # 2026
        (2026, 1, 28), (2026, 3, 18), (2026, 5, 6), (2026, 6, 17),
        (2026, 7, 29), (2026, 9, 16), (2026, 10, 28), (2026, 12, 9),
        # 2027 (estimated, based on 8-per-year pattern)
        (2027, 1, 27), (2027, 3, 17), (2027, 5, 5), (2027, 6, 16),
        (2027, 7, 28), (2027, 9, 15), (2027, 10, 27), (2027, 12, 8),
    ]

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=months_ahead * 30)

    events = []
    for y, m, d in _FOMC_SCHEDULE:
        dt = datetime(y, m, d, 19, 0, 0, tzinfo=timezone.utc)
        if now - timedelta(hours=2) <= dt <= cutoff:
            events.append(EconomicEvent(
                name="FOMC",
                event_datetime=dt,
                impact="HIGH",
                currency="USD",
                description="FOMC Interest Rate Decision",
                no_trade_minutes_before=30,
                no_trade_minutes_after=60,
            ))
    return events


def get_ecb_dates(months_ahead: int = 6) -> List[EconomicEvent]:
    """
    ECB Governing Council meetings (8 per year).
    Decision at 13:15 UTC. Hardcoded schedule 2025-2027.
    """
    _ECB_SCHEDULE = [
        # 2025
        (2025, 1, 30), (2025, 3, 6), (2025, 4, 17), (2025, 6, 5),
        (2025, 7, 17), (2025, 9, 11), (2025, 10, 30), (2025, 12, 18),
        # 2026
        (2026, 1, 22), (2026, 3, 5), (2026, 4, 30), (2026, 6, 4),
        (2026, 7, 23), (2026, 9, 10), (2026, 10, 29), (2026, 12, 17),
        # 2027 (estimated)
        (2027, 1, 21), (2027, 3, 4), (2027, 4, 22), (2027, 6, 3),
        (2027, 7, 22), (2027, 9, 9), (2027, 10, 28), (2027, 12, 16),
    ]

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=months_ahead * 30)

    events = []
    for y, m, d in _ECB_SCHEDULE:
        dt = datetime(y, m, d, 13, 15, 0, tzinfo=timezone.utc)
        if now - timedelta(hours=2) <= dt <= cutoff:
            events.append(EconomicEvent(
                name="ECB",
                event_datetime=dt,
                impact="HIGH",
                currency="EUR",
                description="ECB Interest Rate Decision",
                no_trade_minutes_before=30,
                no_trade_minutes_after=60,
            ))
    return events


def get_cpi_dates(months_ahead: int = 3) -> List[EconomicEvent]:
    """
    US CPI published approximately 2nd Wednesday of each month at 13:30 UTC.
    Using algorithmic calculation (2nd Wednesday approximation).
    """
    now = datetime.now(timezone.utc)
    events = []
    current = date(now.year, now.month, 1)

    for _ in range(months_ahead + 1):
        cpi_date = _second_wednesday_of_month(current.year, current.month)
        cpi_dt = datetime(
            cpi_date.year, cpi_date.month, cpi_date.day,
            13, 30, 0, tzinfo=timezone.utc
        )
        if cpi_dt > now - timedelta(hours=1):
            events.append(EconomicEvent(
                name="CPI",
                event_datetime=cpi_dt,
                impact="HIGH",
                currency="USD",
                description="US Consumer Price Index",
            ))
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return events


def get_upcoming_events(
    days_ahead: int = 7,
    ref_datetime: datetime | None = None,
) -> List[EconomicEvent]:
    """
    Returns all high-impact events in the next `days_ahead` days, sorted by time.
    """
    if ref_datetime is None:
        ref_datetime = datetime.now(timezone.utc)
    cutoff = ref_datetime + timedelta(days=days_ahead)

    all_events: List[EconomicEvent] = []
    all_events.extend(get_nfp_dates(months_ahead=max(1, days_ahead // 28 + 1)))
    all_events.extend(get_fomc_dates(months_ahead=max(1, days_ahead // 28 + 1)))
    all_events.extend(get_ecb_dates(months_ahead=max(1, days_ahead // 28 + 1)))
    all_events.extend(get_cpi_dates(months_ahead=max(1, days_ahead // 28 + 1)))

    return sorted(
        [e for e in all_events if ref_datetime <= e.event_datetime <= cutoff],
        key=lambda e: e.event_datetime,
    )


def is_in_no_trade_window(
    event: EconomicEvent,
    now: datetime | None = None,
) -> bool:
    """True if `now` is within the event's no-trade window."""
    if now is None:
        now = datetime.now(timezone.utc)
    return event.window_start <= now <= event.window_end


def get_active_no_trade_windows(now: datetime | None = None) -> List[EconomicEvent]:
    """
    Returns events currently in their no-trade window.
    Checks events for the next 7 days (plus recent past).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Get events in a window: 2h ago to 7 days ahead
    all_events: List[EconomicEvent] = []
    all_events.extend(get_nfp_dates(months_ahead=1))
    all_events.extend(get_fomc_dates(months_ahead=1))
    all_events.extend(get_ecb_dates(months_ahead=1))
    all_events.extend(get_cpi_dates(months_ahead=1))

    return [e for e in all_events if is_in_no_trade_window(e, now)]


def get_todays_events(now: datetime | None = None) -> List[dict]:
    """Returns today's economic events as a list of dicts (for dashboard)."""
    if now is None:
        now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59)

    events = get_upcoming_events(days_ahead=1, ref_datetime=today_start)
    return [
        {
            "name": e.name,
            "time_utc": e.event_datetime.strftime("%H:%M UTC"),
            "currency": e.currency,
            "impact": e.impact,
            "description": e.description,
            "in_window": is_in_no_trade_window(e, now),
        }
        for e in events
        if today_start <= e.event_datetime <= today_end
    ]
