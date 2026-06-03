import pytest
import os

# Set required env vars before importing project modules
os.environ.setdefault("TRADING_MODE", "PAPER")
os.environ.setdefault("PAPER_CAPITAL", "10000.0")
os.environ.setdefault("RISK_PER_TRADE_PCT", "1.0")
os.environ.setdefault("MAX_DAILY_DRAWDOWN_PCT", "2.0")
os.environ.setdefault("MAX_CONSECUTIVE_LOSSES", "3")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("DATABASE_PATH", ":memory:")
os.environ.setdefault("ACTIVE_BROKER", "paper")
os.environ.setdefault("MIN_CONFIDENCE_THRESHOLD", "0.65")
