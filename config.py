"""
config.py — Centralized system configuration
Reads from .env, provides typed settings with validation.
"""
from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")


class TradingMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class BrokerType(str, Enum):
    OANDA = "oanda"
    IB = "ib"
    MT5 = "mt5"
    PAPER = "paper"


class OandaEnvironment(str, Enum):
    PRACTICE = "practice"
    LIVE = "live"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class SystemConfig(BaseSettings):
    """Master configuration object. All values come from .env or defaults."""

    # ── Trading Mode ──────────────────────────────────────
    trading_mode: TradingMode = Field(default=TradingMode.PAPER, alias="TRADING_MODE")

    # ── Capital ───────────────────────────────────────────
    paper_capital: float = Field(default=10_000.0, alias="PAPER_CAPITAL")
    live_capital_limit: float = Field(default=0.0, alias="LIVE_CAPITAL_LIMIT")

    # ── Risk Parameters ───────────────────────────────────
    risk_per_trade_pct: float = Field(default=1.0, alias="RISK_PER_TRADE_PCT")
    max_daily_drawdown_pct: float = Field(default=2.0, alias="MAX_DAILY_DRAWDOWN_PCT")
    max_consecutive_losses: int = Field(default=3, alias="MAX_CONSECUTIVE_LOSSES")
    profit_transfer_trigger_pct: float = Field(default=5.0, alias="PROFIT_TRANSFER_TRIGGER_PCT")
    min_confidence_threshold: float = Field(default=0.65, alias="MIN_CONFIDENCE_THRESHOLD")

    # ── OANDA ─────────────────────────────────────────────
    oanda_api_key: Optional[str] = Field(default=None, alias="OANDA_API_KEY")
    oanda_account_id: Optional[str] = Field(default=None, alias="OANDA_ACCOUNT_ID")
    oanda_environment: OandaEnvironment = Field(
        default=OandaEnvironment.PRACTICE, alias="OANDA_ENVIRONMENT"
    )

    # ── Interactive Brokers ───────────────────────────────
    ib_host: str = Field(default="127.0.0.1", alias="IB_HOST")
    ib_port: int = Field(default=7497, alias="IB_PORT")
    ib_client_id: int = Field(default=1, alias="IB_CLIENT_ID")

    # ── MT5 ───────────────────────────────────────────────
    mt5_login: int = Field(default=0, alias="MT5_LOGIN")
    mt5_password: Optional[str] = Field(default=None, alias="MT5_PASSWORD")
    mt5_server: Optional[str] = Field(default=None, alias="MT5_SERVER")

    # ── Ollama ────────────────────────────────────────────
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_timeout: int = Field(default=120, alias="OLLAMA_TIMEOUT")
    ollama_model_orchestrator: str = Field(default="llama3.1:8b", alias="OLLAMA_MODEL_ORCHESTRATOR")
    ollama_model_fundamental: str = Field(default="mistral:7b", alias="OLLAMA_MODEL_FUNDAMENTAL")
    ollama_model_risk: str = Field(default="phi3:medium", alias="OLLAMA_MODEL_RISK")
    ollama_model_critic: str = Field(default="llama3.1:8b", alias="OLLAMA_MODEL_CRITIC")
    ollama_model_technical: str = Field(default="phi3:medium", alias="OLLAMA_MODEL_TECHNICAL")

    # ── Database ──────────────────────────────────────────
    database_path: str = Field(default="./trading_system.db", alias="DATABASE_PATH")
    db_backup_interval_hours: int = Field(default=6, alias="DB_BACKUP_INTERVAL_HOURS")

    # ── Telegram ──────────────────────────────────────────
    telegram_token: Optional[str] = Field(default=None, alias="TELEGRAM_TOKEN")
    telegram_chat_id: Optional[str] = Field(default=None, alias="TELEGRAM_CHAT_ID")
    telegram_enabled: bool = Field(default=False, alias="TELEGRAM_ENABLED")

    # ── Logging ───────────────────────────────────────────
    log_level: LogLevel = Field(default=LogLevel.INFO, alias="LOG_LEVEL")
    log_dir: str = Field(default="./logs", alias="LOG_DIR")

    # ── Broker ────────────────────────────────────────────
    active_broker: BrokerType = Field(default=BrokerType.PAPER, alias="ACTIVE_BROKER")

    # ── Trading Symbol ────────────────────────────────────
    trading_symbol: str = Field(default="EUR_USD", alias="TRADING_SYMBOL")
    trading_interval_minutes: int = Field(default=15, alias="TRADING_INTERVAL_MINUTES")

    # ── API Server ────────────────────────────────────────
    api_enabled: bool = Field(default=True, description="Enable the web dashboard API", alias="API_ENABLED")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8080, alias="API_PORT")

    # ── Trading Cycle ─────────────────────────────────────
    cycle_interval_seconds: int = Field(default=300, description="Seconds between trading cycles", alias="CYCLE_INTERVAL_SECONDS")
    cycle_timeout_seconds: int = Field(default=300, description="Max cycle duration before watchdog fires", alias="CYCLE_TIMEOUT_SECONDS")

    # ── Multi-pair ────────────────────────────────────────
    additional_symbols: List[str] = Field(default_factory=list, description="Additional trading symbols", alias="ADDITIONAL_SYMBOLS")

    # ── Backtesting Defaults ──────────────────────────────
    backtest_default_start: str = Field(default="2024-01-01", alias="BACKTEST_DEFAULT_START")
    backtest_default_end: str = Field(default="2024-12-31", alias="BACKTEST_DEFAULT_END")

    @field_validator("risk_per_trade_pct")
    @classmethod
    def validate_risk(cls, v: float) -> float:
        if not 0.1 <= v <= 5.0:
            raise ValueError("RISK_PER_TRADE_PCT must be between 0.1 and 5.0")
        return v

    @field_validator("max_daily_drawdown_pct")
    @classmethod
    def validate_drawdown(cls, v: float) -> float:
        if not 0.5 <= v <= 10.0:
            raise ValueError("MAX_DAILY_DRAWDOWN_PCT must be between 0.5 and 10.0")
        return v

    @field_validator("min_confidence_threshold")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("MIN_CONFIDENCE_THRESHOLD must be between 0.0 and 1.0")
        return v

    @property
    def is_live(self) -> bool:
        return self.trading_mode == TradingMode.LIVE

    @property
    def is_paper(self) -> bool:
        return self.trading_mode == TradingMode.PAPER

    @property
    def effective_broker(self) -> BrokerType:
        """Force paper broker if mode is PAPER."""
        if self.is_paper:
            return BrokerType.PAPER
        return self.active_broker

    @property
    def yahoo_symbol(self) -> str:
        """Convert broker symbol format to Yahoo Finance format."""
        return self.trading_symbol.replace("_", "") + "=X"

    model_config = {"populate_by_name": True}


@lru_cache(maxsize=1)
def get_config() -> SystemConfig:
    """Return cached singleton config instance."""
    return SystemConfig()


def reset_config() -> None:
    """Clear config cache (useful for testing)."""
    get_config.cache_clear()
