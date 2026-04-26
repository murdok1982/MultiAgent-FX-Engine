"""
utils/ollama_client.py — Async Ollama REST client with retry, circuit-breaker,
and strict JSON extraction with Pydantic validation.
"""
from __future__ import annotations

import asyncio
import json
import time
from enum import Enum
from typing import Any, Dict, Optional, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from config import get_config
from utils.logger import get_logger

logger = get_logger("ollama_client")

T = TypeVar("T", bound=BaseModel)

# ── Circuit Breaker State ─────────────────────────────────────────────────────

class CircuitState(str, Enum):
    CLOSED = "CLOSED"       # Normal operation
    OPEN = "OPEN"           # Failures threshold exceeded → fast-fail
    HALF_OPEN = "HALF_OPEN" # Testing recovery


class CircuitBreaker:
    """Simple per-model circuit breaker."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None

    def record_success(self) -> None:
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(f"Circuit breaker OPEN after {self.failure_count} failures")

    def can_proceed(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.monotonic() - (self.last_failure_time or 0)
            if elapsed >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN — allow one test request


_breakers: Dict[str, CircuitBreaker] = {}


def _get_breaker(model: str) -> CircuitBreaker:
    if model not in _breakers:
        _breakers[model] = CircuitBreaker()
    return _breakers[model]


# ── Prompt Helpers ────────────────────────────────────────────────────────────

def _build_json_system_prompt(schema_description: str) -> str:
    return (
        "You are a trading AI agent. You MUST respond ONLY with valid JSON. "
        "No markdown, no explanation, no code blocks. Just raw JSON. "
        f"Schema: {schema_description}"
    )


def _extract_json(text: str) -> Dict[str, Any]:
    """Extract JSON from model output, handling markdown code blocks."""
    text = text.strip()
    # Remove code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(
            line for line in lines
            if not line.startswith("```")
        ).strip()
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Find first { ... } block
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not extract JSON from: {text[:200]}")


# ── Main Client ───────────────────────────────────────────────────────────────

class OllamaClient:
    """Async Ollama client with retry + circuit breaker."""

    def __init__(self):
        cfg = get_config()
        self.base_url = cfg.ollama_base_url
        self.timeout = cfg.ollama_timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def generate(
        self,
        model: str,
        prompt: str,
        system: Optional[str] = None,
        max_retries: int = 3,
    ) -> str:
        """
        Send a generation request to Ollama with retry + circuit breaker.
        Returns raw text response.
        """
        breaker = _get_breaker(model)

        if not breaker.can_proceed():
            logger.warning(f"Circuit breaker OPEN for {model} — returning empty string")
            return ""

        cfg = get_config()
        timeout_secs: int = getattr(cfg, "ollama_timeout", 30)

        payload: Dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.1, "seed": 42},
        }
        if system:
            payload["system"] = system

        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                start = time.monotonic()
                client = await self._get_client()
                async with asyncio.timeout(timeout_secs):
                    resp = await client.post(
                        f"{self.base_url}/api/generate",
                        json=payload,
                    )
                resp.raise_for_status()
                data = resp.json()
                response_text = data.get("response", "")
                latency = (time.monotonic() - start) * 1000
                logger.debug(f"Ollama [{model}] OK in {latency:.0f}ms")
                breaker.record_success()
                return response_text

            except TimeoutError as e:
                last_error = e
                breaker.record_failure()
                logger.warning(
                    f"Ollama [{model}] attempt {attempt}/{max_retries} timed out after {timeout_secs}s"
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                last_error = e
                breaker.record_failure()
                logger.warning(f"Ollama [{model}] attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                last_error = e
                breaker.record_failure()
                logger.error(f"Ollama [{model}] unexpected error: {e}")
                break

        logger.error(f"Ollama [{model}] all retries exhausted: {last_error}")
        return ""

    async def generate_json(
        self,
        model: str,
        prompt: str,
        schema_class: Type[T],
        system: Optional[str] = None,
        max_retries: int = 3,
    ) -> Optional[T]:
        """
        Generate a JSON response and validate it against a Pydantic schema.
        Returns None if Ollama is unavailable or JSON is invalid.
        """
        sys_prompt = _build_json_system_prompt(
            schema_class.model_json_schema().__str__()
        ) if system is None else system

        raw = await self.generate(model, prompt, sys_prompt, max_retries)
        if not raw:
            logger.warning(f"Ollama [{model}] returned empty — using schema defaults")
            return None

        try:
            data = _extract_json(raw)
            return schema_class(**data)
        except (ValueError, ValidationError, TypeError) as e:
            logger.error(f"Ollama [{model}] JSON validation failed: {e}\nRaw: {raw[:300]}")
            return None

    async def health_check(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list:
        """Return list of locally available models."""
        try:
            client = await self._get_client()
            resp = await client.get(f"{self.base_url}/api/tags", timeout=10.0)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except Exception as e:
            logger.error(f"Failed to list Ollama models: {e}")
            return []


# Singleton
_client_instance: Optional[OllamaClient] = None


def get_ollama_client() -> OllamaClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = OllamaClient()
    return _client_instance
