"""
agents/social_sentiment.py — Social Media Sentiment Agent (X + Reddit)
======================================================================

Aggregates retail / social sentiment around EUR, USD, ECB, Fed, EURUSD.

Sources (graceful degradation — works even if all APIs are missing):
  • X (Twitter) v2 API     — needs X_BEARER_TOKEN
  • Reddit via PRAW        — needs REDDIT_CLIENT_ID / SECRET / USER_AGENT
  • Reddit public JSON     — no auth, soft fallback
  • StockTwits public API  — no auth, fallback

Scoring:
  • Per-post sentiment via VADER (if installed) else keyword scoring.
  • Aggregated score in [-1.0, +1.0].
  • Volume-weighted to penalise tiny samples.
  • Crowd-contrarian flag: extreme retail consensus → fade signal.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from agents.base_agent import BaseAgent


# ── Schema ────────────────────────────────────────────────────────────────────

class SocialSentimentSignal(BaseModel):
    score: float = Field(default=0.0, ge=-1.0, le=1.0)
    label: str = Field(default="NEUTRAL", description="BULLISH | BEARISH | NEUTRAL")
    sample_size: int = 0
    x_posts: int = 0
    reddit_posts: int = 0
    stocktwits_posts: int = 0
    crowd_extreme: bool = False
    contrarian_bias: str = Field(default="NONE", description="BUY | SELL | NONE")
    top_themes: List[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("label")
    @classmethod
    def _l(cls, v):
        v = v.upper()
        return v if v in ("BULLISH", "BEARISH", "NEUTRAL") else "NEUTRAL"


# ── Keyword fallback scoring ──────────────────────────────────────────────────

_BULL_KEYWORDS = {
    "long", "buy", "bullish", "moon", "pump", "breakout", "rally",
    "ecb hawkish", "fed dovish", "weak dollar", "dxy down",
    "eur strong", "rate cut fed", "rate hike ecb",
}
_BEAR_KEYWORDS = {
    "short", "sell", "bearish", "dump", "crash", "breakdown", "drop",
    "ecb dovish", "fed hawkish", "strong dollar", "dxy up",
    "eur weak", "rate hike fed", "rate cut ecb",
}


def _score_text_keywords(text: str) -> float:
    t = text.lower()
    pos = sum(1 for k in _BULL_KEYWORDS if k in t)
    neg = sum(1 for k in _BEAR_KEYWORDS if k in t)
    if pos == 0 and neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def _score_text(text: str) -> float:
    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        if not hasattr(_score_text, "_vader"):
            _score_text._vader = SentimentIntensityAnalyzer()
        v = _score_text._vader.polarity_scores(text)["compound"]
        kw = _score_text_keywords(text)
        # Average vader (general) with FX-keyword score (domain)
        return 0.5 * v + 0.5 * kw
    except Exception:
        return _score_text_keywords(text)


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _fetch_x(query: str, limit: int = 30) -> List[Tuple[str, datetime]]:
    """X (Twitter) v2 search via bearer token, with safe fallback."""
    token = os.getenv("X_BEARER_TOKEN", "").strip()
    if not token:
        return []
    try:
        import requests
        r = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "query": f"({query}) -is:retweet lang:en",
                "max_results": str(min(limit, 100)),
                "tweet.fields": "created_at",
            },
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json().get("data", [])
        out = []
        for t in data:
            ts = datetime.fromisoformat(t.get("created_at", "").replace("Z", "+00:00"))
            out.append((t["text"], ts))
        return out
    except Exception:
        return []


def _fetch_reddit(subreddits: List[str], query: str, limit: int = 30
                 ) -> List[Tuple[str, datetime]]:
    """Reddit via PRAW if configured, else public JSON."""
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.getenv("REDDIT_USER_AGENT", "multiagent-fx/1.0").strip()

    posts: List[Tuple[str, datetime]] = []
    if client_id and client_secret:
        try:
            import praw
            reddit = praw.Reddit(
                client_id=client_id,
                client_secret=client_secret,
                user_agent=user_agent,
            )
            for sub in subreddits:
                for p in reddit.subreddit(sub).search(query, limit=limit, sort="new"):
                    ts = datetime.fromtimestamp(p.created_utc, tz=timezone.utc)
                    posts.append((f"{p.title}. {p.selftext}", ts))
            return posts
        except Exception:
            pass

    # Public JSON fallback
    try:
        import requests
        for sub in subreddits:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/search.json",
                params={"q": query, "restrict_sr": "1", "sort": "new", "limit": str(limit)},
                headers={"User-Agent": user_agent},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            for child in r.json().get("data", {}).get("children", []):
                d = child.get("data", {})
                ts = datetime.fromtimestamp(d.get("created_utc", 0), tz=timezone.utc)
                posts.append((f"{d.get('title','')}. {d.get('selftext','')}", ts))
    except Exception:
        pass
    return posts


def _fetch_stocktwits(symbols: List[str]) -> List[Tuple[str, datetime]]:
    """StockTwits public stream — no auth needed."""
    out: List[Tuple[str, datetime]] = []
    try:
        import requests
        for sym in symbols:
            r = requests.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json",
                timeout=10,
            )
            if r.status_code != 200:
                continue
            for m in r.json().get("messages", []):
                ts_str = m.get("created_at")
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    ts = datetime.now(timezone.utc)
                out.append((m.get("body", ""), ts))
    except Exception:
        pass
    return out


# ── Theme extraction ──────────────────────────────────────────────────────────

_THEME_PATTERNS = [
    ("ECB", re.compile(r"\becb\b", re.I)),
    ("Fed", re.compile(r"\b(fed|fomc|powell)\b", re.I)),
    ("Lagarde", re.compile(r"\blagarde\b", re.I)),
    ("CPI", re.compile(r"\bcpi\b", re.I)),
    ("NFP", re.compile(r"\bnfp\b|non[- ]?farm", re.I)),
    ("DXY", re.compile(r"\bdxy\b|dollar index", re.I)),
    ("Rate cut", re.compile(r"rate cut", re.I)),
    ("Rate hike", re.compile(r"rate hike", re.I)),
    ("Recession", re.compile(r"recession", re.I)),
]


def _extract_themes(texts: List[str], top: int = 5) -> List[str]:
    counts: dict[str, int] = {}
    for t in texts:
        for name, pat in _THEME_PATTERNS:
            if pat.search(t):
                counts[name] = counts.get(name, 0) + 1
    return [k for k, _ in sorted(counts.items(), key=lambda x: -x[1])[:top]]


# ── Agent ─────────────────────────────────────────────────────────────────────

class SocialSentimentAgent(BaseAgent):
    name = "SocialSentimentAgent"

    QUERY_X = '"EURUSD" OR "EUR/USD" OR "EURO USD" OR ECB OR FOMC'
    QUERY_REDDIT = "EURUSD OR EUR/USD OR ECB OR Fed"
    SUBS = ["Forex", "Daytrading", "Trading", "wallstreetbets", "investing"]
    STOCKTWITS_SYMBOLS = ["EURUSD", "DXY"]

    def __init__(self, config=None, max_age_hours: int = 24):
        super().__init__(config)
        self.model = "vader+keyword"
        self.max_age = timedelta(hours=max_age_hours)

    def _default_output(self) -> SocialSentimentSignal:
        return SocialSentimentSignal(reason="Social sentiment agent unavailable")

    async def analyze(self, **kw) -> SocialSentimentSignal:
        now = datetime.now(timezone.utc)
        x = _fetch_x(self.QUERY_X)
        rd = _fetch_reddit(self.SUBS, self.QUERY_REDDIT)
        st = _fetch_stocktwits(self.STOCKTWITS_SYMBOLS)

        # Filter freshness
        x = [(t, ts) for t, ts in x if now - ts <= self.max_age]
        rd = [(t, ts) for t, ts in rd if now - ts <= self.max_age]
        st = [(t, ts) for t, ts in st if now - ts <= self.max_age]

        all_texts = [t for t, _ in (x + rd + st)]
        n = len(all_texts)

        if n == 0:
            return SocialSentimentSignal(
                score=0.0, label="NEUTRAL", sample_size=0,
                reason="No social posts available (set X_BEARER_TOKEN / REDDIT_* envs)",
            )

        scores = [_score_text(t) for t in all_texts]
        raw_score = sum(scores) / max(len(scores), 1)
        # Volume-weighted: small samples get pulled toward 0
        weight = min(1.0, n / 40.0)
        score = raw_score * weight

        if score >= 0.15:
            label = "BULLISH"
        elif score <= -0.15:
            label = "BEARISH"
        else:
            label = "NEUTRAL"

        # Crowd-extreme contrarian: very strong retail consensus is often a fade
        crowd_extreme = abs(raw_score) >= 0.55 and n >= 25
        contrarian = "NONE"
        if crowd_extreme:
            contrarian = "SELL" if raw_score > 0 else "BUY"

        themes = _extract_themes(all_texts)
        reason = (
            f"n={n} (X={len(x)}, Reddit={len(rd)}, StockTwits={len(st)}) "
            f"raw={raw_score:+.2f} weighted={score:+.2f} "
            f"themes={','.join(themes) if themes else '—'}"
        )
        self.logger.info(f"Social: {label} score={score:+.2f} | {reason}")

        return SocialSentimentSignal(
            score=round(score, 3),
            label=label,
            sample_size=n,
            x_posts=len(x),
            reddit_posts=len(rd),
            stocktwits_posts=len(st),
            crowd_extreme=crowd_extreme,
            contrarian_bias=contrarian,
            top_themes=themes,
            reason=reason,
        )
