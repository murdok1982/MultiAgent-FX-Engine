"""
data/news_fetcher.py — News sentiment + economic calendar with HIGH IMPACT detection.
Primary: Yahoo Finance news scraper.
Calendar: Dynamic (calculated algorithmically) via utils.economic_calendar.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import yfinance as yf

from utils.logger import get_logger

# Dynamic calendar — imported with fallback for parallel-agent scenarios
try:
    from utils.economic_calendar import (
        get_active_no_trade_windows,
        get_upcoming_events,
        get_todays_events,
    )
except ImportError:
    # Fallback stubs if utils.economic_calendar is not yet available
    def get_active_no_trade_windows(now=None):  # type: ignore[misc]
        return []

    def get_upcoming_events(days_ahead: int = 7, ref_datetime=None):  # type: ignore[misc]
        return []

    def get_todays_events(now=None):  # type: ignore[misc]
        return []

# Sanitization — imported with inline fallback
try:
    from utils.security import sanitize_for_prompt as _sanitize
except ImportError:
    def _sanitize(text: str, max_len: int = 300) -> str:  # type: ignore[misc]
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(text or ''))
        return text[:max_len]

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
                raw_title = n.get("content", {}).get("title", "") or n.get("title", "")
                raw_summary = n.get("content", {}).get("summary", "") or n.get("summary", "")
                pub_raw = n.get("content", {}).get("pubDate", "") or n.get("pubDate", "")

                # Sanitize before storing
                title = _sanitize(raw_title, max_len=200)
                summary = _sanitize(raw_summary, max_len=400)

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
                    summary=summary,
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
    calendar: List[CalendarEvent] = None,  # noqa: ARG001 — kept for API compatibility
    buffer_minutes: int = HIGH_IMPACT_BUFFER_MINUTES,  # noqa: ARG001 — window defined per-event
) -> tuple[bool, str]:
    """
    Check if current time is within the no-trade buffer of any HIGH impact event.
    Uses the dynamic economic calendar. `calendar` and `buffer_minutes` parameters
    are retained for API compatibility but ignored — per-event windows apply.
    Returns (in_window, description).
    """
    active_windows = get_active_no_trade_windows()
    if active_windows:
        desc = ", ".join(e.name for e in active_windows)
        return True, f"No-trade window: {desc}"
    return False, ""


# ── Main Analyzer ──────────────────────────────────────────────────────────────

class NewsFetcher:
    """
    Unified news + calendar analyzer.
    Enforces NO-TRADE window around HIGH IMPACT events.
    Calendar dates are calculated dynamically — no hardcoded specific dates.
    """

    def analyze(self) -> NewsAnalysis:
        # 1. Fetch and sanitize news
        news = fetch_yahoo_news()

        # 2. Check high-impact calendar window (dynamic)
        now = datetime.now(timezone.utc)
        active_windows = get_active_no_trade_windows(now)
        no_trade_window = bool(active_windows)
        no_trade_reason = ""
        if active_windows:
            no_trade_reason = (
                f"No-trade window: {', '.join(e.name for e in active_windows)}"
            )

        # 3. Aggregate sentiment
        if news:
            scores = [n.sentiment_score for n in news]
            avg_sentiment = round(sum(scores) / len(scores), 3)
        else:
            avg_sentiment = 0.0

        has_high_impact = any(n.is_high_impact for n in news) or no_trade_window

        # 4. Upcoming events (next 3 days) — converted to CalendarEvent for compatibility
        upcoming_eco = get_upcoming_events(days_ahead=3, ref_datetime=now)
        upcoming: List[CalendarEvent] = [
            CalendarEvent(
                name=e.name,
                datetime_utc=e.event_datetime,
                impact=e.impact,
                currency=e.currency,
            )
            for e in upcoming_eco
        ]

        return NewsAnalysis(
            news_items=news,
            calendar_events=upcoming,
            sentiment_score=avg_sentiment,
            has_high_impact_event=has_high_impact,
            high_impact_description=no_trade_reason if no_trade_window else (
                f"Upcoming: {upcoming[0].name}" if upcoming else ""
            ),
            no_trade_window=no_trade_window,
            no_trade_reason=no_trade_reason,
        )

    def get_calendar_events_today(self) -> list[dict]:
        """Returns today's economic events as dicts for dashboard display."""
        return get_todays_events()
