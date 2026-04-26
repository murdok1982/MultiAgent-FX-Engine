"""Tests for CircuitBreaker in utils/ollama_client.py.

All tests are purely in-memory — no Ollama server required.
"""
import time

import pytest

from utils.ollama_client import CircuitBreaker, CircuitState


class TestCircuitBreakerInitialState:
    def test_initial_state_closed(self):
        breaker = CircuitBreaker()
        assert breaker.state == CircuitState.CLOSED

    def test_initial_can_proceed(self):
        breaker = CircuitBreaker()
        assert breaker.can_proceed() is True

    def test_initial_failure_count_zero(self):
        breaker = CircuitBreaker()
        assert breaker.failure_count == 0


class TestCircuitBreakerOpens:
    def test_opens_after_threshold(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

    def test_open_blocks_proceed(self):
        breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            breaker.record_failure()
        assert breaker.can_proceed() is False

    def test_does_not_open_before_threshold(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
        for _ in range(4):
            breaker.record_failure()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.can_proceed() is True


class TestCircuitBreakerSuccessReset:
    def test_success_resets_failure_count(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        assert breaker.failure_count == 0

    def test_success_keeps_state_closed(self):
        breaker = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
        breaker.record_failure()
        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED


class TestCircuitBreakerHalfOpen:
    def test_half_open_after_recovery_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=1)
        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN

        # Wind back last_failure_time so timeout appears elapsed
        breaker.last_failure_time = time.monotonic() - 2  # 2s ago > 1s timeout

        result = breaker.can_proceed()
        assert result is True
        assert breaker.state == CircuitState.HALF_OPEN

    def test_open_still_blocks_before_timeout(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=9999)
        breaker.record_failure()
        assert breaker.can_proceed() is False
        assert breaker.state == CircuitState.OPEN


class TestCircuitBreakerClosesFromHalfOpen:
    def test_closes_on_success_in_half_open(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=1)
        breaker.record_failure()
        # Force transition to HALF_OPEN
        breaker.last_failure_time = time.monotonic() - 2
        breaker.can_proceed()  # triggers OPEN → HALF_OPEN transition
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_success()
        assert breaker.state == CircuitState.CLOSED
        assert breaker.failure_count == 0

    def test_reopens_on_failure_in_half_open(self):
        breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=1)
        breaker.record_failure()
        breaker.last_failure_time = time.monotonic() - 2
        breaker.can_proceed()  # OPEN → HALF_OPEN
        assert breaker.state == CircuitState.HALF_OPEN

        breaker.record_failure()
        assert breaker.state == CircuitState.OPEN
