"""
data/news_fetcher.py — News sentiment + economic calendar with HIGH IMPACT detection.
Primary: Yahoo Finance news scraper.
Calendar: Mock (hardcoded 2025-2026 dates) + extensible for live APIs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import yfinance as yf

from utils.logger import get_logger

logger = get_logger("news_fetcher")

# ── Event Types ────────────────────────────────────────────────────────────────

HIGH_IMPACT_KEYWORDS = [
    "nonfarm", "non-farm", "NFP", "payroll",    # NFP
    "CPI", "inflation", "consumer price",        # CPI
    "FOMC", "federal reserve", "fed rate",       # FOMC
    "ECB", "european central bank", "rate decision",  # ECB
    "GDP", "unemployment", "retail sales",       # Other major
]

BULLISH_EUR_KEYWORDS = [
    "ecb hike", "rate hike", "hawkish", "strong euro",
    "beat expectations", "better than expected", "euro rises",
    "eur gains", "dollar falls", "risk on",
]

BEARISH_EUR_KEYWORDS = [
    "ecb cut", "rate cut", "dovish", "weak euro",
    "miss expectations", "disappoints", "euro falls",
    "eur weakens", "dollar rises", "risk off", "recession",
]

# High-impact event buffer in minutes
HIGH_IMPACT_BUFFER_MINUTES = 30


@dataclass
class NewsItem:
    title: str
    summary: str
    published: datetime
    source: str
    sentiment_score: float = 0.0  # -1.0 to +1.0
    is_high_impact: bool = False


@dataclass
class CalendarEvent:
    name: str
    datetime_utc: datetime
    impact: str  # HIGH | MEDIUM | LOW
    currency: str
    forecast: Optional[str] = None
    previous: Optional[str] = None


@dataclass
class NewsAnalysis:
    news_items: List[NewsItem] = field(default_factory=list)
    calendar_events: List[CalendarEvent] = field(default_factory=list)
    sentiment_score: float = 0.0  # Aggregate -1 to +1
    has_high_impact_event: bool = False
    high_impact_description: str = ""
    no_trade_window: bool = False
    no_trade_reason: str = ""


# ── Calendar (Mock) ────────────────────────────────────────────────────────────

def _build_mock_calendar() -> List[CalendarEvent]:
    """
    Pre-populated 2026 economic calendar events (HIGH impact).
    Extend or replace with a live calendar API (e.g., ForexFactory, Investing.com).
    """
    events = []
    # NFP: First Friday of each month at 13:30 UTC
    nfp_dates_2026 = [
        datetime(2026, 1, 2, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 2, 6, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 3, 6, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 4, 3, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 1, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 6, 5, 13, 30, tzinfo=timezone.utc),
    ]
    for d in nfp_dates_2026:
        events.append(CalendarEvent("Non-Farm Payrolls (NFP)", d, "HIGH", "USD"))

    # FOMC meetings (approx)
    fomc_dates = [
        datetime(2026, 1, 28, 19, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 18, 19, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 6, 19, 0, tzinfo=timezone.utc),
    ]
    for d in fomc_dates:
        events.append(CalendarEvent("FOMC Rate Decision", d, "HIGH", "USD"))

    # ECB meetings (approx)
    ecb_dates = [
        datetime(2026, 1, 22, 13, 15, tzinfo=timezone.utc),
        datetime(2026, 3, 5, 13, 15, tzinfo=timezone.utc),
        datetime(2026, 4, 30, 13, 15, tzinfo=timezone.utc),
    ]
    for d in ecb_dates:
        events.append(CalendarEvent("ECB Rate Decision", d, "HIGH", "EUR"))

    # US CPI (approx 2nd Tuesday each month)
    cpi_dates = [
        datetime(2026, 1, 14, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 2, 11, 13, 30, tzinfo=timezone.utc),
        datetime(2026, 3, 11, 12, 30, tzinfo=timezone.utc),
    ]
    for d in cpi_dates:
        events.append(CalendarEvent("US CPI (Inflation)", d, "HIGH", "USD"))

    return events


_MOCK_CALENDAR = _build_mock_calendar()


# ── Sentiment Scoring ──────────────────────────────────────────────────────────

def _score_text(text: str) -> float:
    """Simple keyword-based sentiment scorer. Returns -1 to +1."""
    text_lower = text.lower()
    bull_hits = sum(1 for kw in BULLISH_EUR_KEYWORDS if kw in text_lower)
    bear_hits = sum(1 for kw in BEARISH_EUR_KEYWORDS if kw in text_lower)
    total = bull_hits + bear_hits
    if total == 0:
        return 0.0
    return round((bull_hits - bear_hits) / total, 3)


def _is_high_impact(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in HIGH_IMPACT_KEYWORDS)


# ── Fetchers ───────────────────────────────────────────────────────────────────

def fetch_yahoo_news(symbols: List[str] = None) -> List[NewsItem]:
    """Fetch recent news from Yahoo Finance for EUR/USD-related symbols."""
    if symbols is None:
        symbols = ["EURUSD=X", "^GSPC", "DX-Y.NYB"]

    items = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            raw_news = ticker.news or []
            for n in raw_news[:5]:  # Limit to 5 per symbol
                title = n.get("content", {}).get("title", "") or n.get("title", "")
                summary = n.get("content", {}).get("summary", "") or n.get("summary", "")
                pub_raw = n.get("content", {}).get("pubDate", "") or n.get("pubDate", "")

                try:
                    if isinstance(pub_raw, (int, float)):
                        pub_dt = datetime.fromtimestamp(pub_raw, tz=timezone.utc)
                    else:
                        pub_dt = datetime.now(timezone.utc)
                except Exception:
                    pub_dt = datetime.now(timezone.utc)

                full_text = f"{title} {summary}"
                score = _score_text(full_text)
                high_impact = _is_high_impact(full_text)

                items.append(NewsItem(
                    title=title,
                    summary=summary[:300],
                    published=pub_dt,
                    source=f"yahoo/{sym}",
                    sentiment_score=score,
                    is_high_impact=high_impact,
                ))
        except Exception as e:
            logger.warning(f"Yahoo news fetch failed for {sym}: {e}")

    # Sort by recency
    items.sort(key=lambda x: x.published, reverse=True)
    return items[:20]


def check_high_impact_window(
    calendar: List[CalendarEvent] = None,
    buffer_minutes: int = HIGH_IMPACT_BUFFER_MINUTES,
) -> tuple[bool, str]:
    """
    Check if current time is within the no-trade buffer of any HIGH impact event.
    Returns (in_window, description).
    """
    if calendar is None:
        calendar = _MOCK_CALENDAR

    now = datetime.now(timezone.utc)
    buffer = timedelta(minutes=buffer_minutes)

    for evt in calendar:
        if evt.impact != "HIGH":
            continue
        if (evt.datetime_utc - buffer) <= now <= (evt.datetime_utc + buffer):
            mins_to_event = int((evt.datetime_utc - now).total_seconds() / 60)
            direction = "before" if mins_to_event > 0 else "after"
            desc = f"{evt.name} ({abs(mins_to_event)} min {direction})"
            return True, desc

    return False, ""


# ── Main Analyzer ──────────────────────────────────────────────────────────────

class NewsFetcher:
    """
    Unified news + calendar analyzer.
    Enforces NO-TRADE window around HIGH IMPACT events.
    """

    def __init__(self):
        self._calendar = _MOCK_CALENDAR

    def analyze(self) -> NewsAnalysis:
        # 1. Fetch news
        news = fetch_yahoo_news()

        # 2. Check high-impact calendar window
        in_window, window_desc = check_high_impact_window(self._calendar)

        # 3. Aggregate sentiment
        if news:
            scores = [n.sentiment_score for n in news]
            avg_sentiment = round(sum(scores) / len(scores), 3)
        else:
            avg_sentiment = 0.0

        has_high_impact = any(n.is_high_impact for n in news) or in_window

        # 4. Upcoming events (next 24h)
        now = datetime.now(timezone.utc)
        upcoming = [
            e for e in self._calendar
            if now <= e.datetime_utc <= now + timedelta(hours=24) and e.impact == "HIGH"
        ]

        return NewsAnalysis(
            news_items=news,
            calendar_events=upcoming,
            sentiment_score=avg_sentiment,
            has_high_impact_event=has_high_impact,
            high_impact_description=window_desc if in_window else (
                f"Upcoming: {upcoming[0].name}" if upcoming else ""
            ),
            no_trade_window=in_window,
            no_trade_reason=window_desc if in_window else "",
        )
