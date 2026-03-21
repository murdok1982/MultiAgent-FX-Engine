"""
agents/base_agent.py — Abstract base class for all trading agents.
Provides common interface, retry wrapper, and logging integration.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic import BaseModel

from config import SystemConfig, get_config
from utils.logger import get_logger, log_agent_event
from utils.ollama_client import OllamaClient, get_ollama_client

T = TypeVar("T", bound=BaseModel)


class AgentResult(BaseModel):
    """Base result wrapper for all agent outputs."""
    agent_name: str
    model: str
    confidence: float = 0.0
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    raw_output: Optional[str] = None


class BaseAgent(ABC):
    """
    Abstract base for all trading system agents.
    Subclasses must implement `analyze()` returning their specific result type.
    """

    name: str = "BaseAgent"
    model: str = "unknown"

    def __init__(self, config: Optional[SystemConfig] = None):
        self.config = config or get_config()
        self.logger = get_logger(self.name.lower())
        self.ollama: OllamaClient = get_ollama_client()
        self._call_count = 0
        self._error_count = 0

    @abstractmethod
    async def analyze(self, **kwargs: Any) -> Any:
        """Run the agent's analysis. Must be implemented by subclasses."""
        ...

    async def safe_analyze(self, **kwargs: Any) -> Any:
        """
        Wrapped analyze() with timing, error capture, and retry.
        Falls back to default output if LLM is unavailable.
        """
        start = time.monotonic()
        self._call_count += 1
        try:
            result = await self.analyze(**kwargs)
            latency = (time.monotonic() - start) * 1000
            log_agent_event(
                self.logger,
                self.name,
                "analysis_complete",
                latency_ms=latency,
            )
            return result
        except Exception as e:
            self._error_count += 1
            self.logger.error(f"[{self.name}] Analysis failed: {e}", exc_info=True)
            return self._default_output()

    @abstractmethod
    def _default_output(self) -> Any:
        """Returns a safe default (HOLD/neutral) output when analysis fails."""
        ...

    async def generate_json(
        self,
        prompt: str,
        schema_class: Type[T],
        system: Optional[str] = None,
    ) -> Optional[T]:
        """Convenience wrapper: call Ollama and validate JSON output."""
        return await self.ollama.generate_json(
            model=self.model,
            prompt=prompt,
            schema_class=schema_class,
            system=system,
        )

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "agent": self.name,
            "calls": self._call_count,
            "errors": self._error_count,
            "error_rate": round(self._error_count / max(self._call_count, 1), 3),
        }
