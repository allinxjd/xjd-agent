"""Tests for gateway.core.resilience — 重试/断路器/限流/超时."""

import asyncio
import pytest
import time

from gateway.core.resilience import (
    RetryConfig,
    retry,
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitState,
    CircuitOpenError,
    TokenBucket,
    timeout,
    fallback,
    Bulkhead,
    BulkheadFullError,
    HealthChecker,
)


# ─── Retry ───

class TestRetry:
    @pytest.mark.asyncio
    async def test_retry_success_first_try(self):
        call_count = 0

        @retry(max_retries=3, base_delay=0.01)
        async def good_func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await good_func()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retry_success_after_failures(self):
        call_count = 0

        @retry(max_retries=3, base_delay=0.01)
        async def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("fail")
            return "ok"

        result = await flaky_func()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retry_exhausted(self):
        @retry(max_retries=2, base_delay=0.01)
        async def bad_func():
            raise RuntimeError("always fail")

        with pytest.raises(RuntimeError, match="always fail"):
            await bad_func()

    @pytest.mark.asyncio
    async def test_retry_specific_exception(self):
        config = RetryConfig(max_retries=3, base_delay=0.01, retry_on=(ValueError,))

        @retry(config)
        async def func():
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            await func()


# ─── Circuit Breaker ───

class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreaker("test")
        assert cb.state == CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_failures(self):
        cb = CircuitBreaker("test", CircuitBreakerConfig(failure_threshold=3))
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False

    def test_half_open_after_recovery(self):
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=2, recovery_timeout=0.01
        ))
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN
        assert cb.allow_request() is True

    def test_closes_after_success_in_half_open(self):
        cb = CircuitBreaker("test", CircuitBreakerConfig(
            failure_threshold=2, recovery_timeout=0.01, success_threshold=1
        ))
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.02)
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_stats(self):
        cb = CircuitBreaker("my_cb")
        stats = cb.get_stats()
        assert stats["name"] == "my_cb"
        assert stats["state"] == "closed"


# ─── Token Bucket ───

class TestTokenBucket:
    def test_consume(self):
        bucket = TokenBucket(rate=100, capacity=10)
        assert bucket.consume() is True

    def test_exhaust(self):
        bucket = TokenBucket(rate=0.001, capacity=2)
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is False

    def test_refill(self):
        bucket = TokenBucket(rate=1000, capacity=5)
        for _ in range(5):
            bucket.consume()
        assert bucket.consume() is False
        time.sleep(0.01)
        assert bucket.consume() is True


# ─── Timeout ───

class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_ok(self):
        @timeout(1.0)
        async def fast():
            return "done"

        assert await fast() == "done"

    @pytest.mark.asyncio
    async def test_timeout_exceeded(self):
        @timeout(0.01)
        async def slow():
            await asyncio.sleep(1)

        with pytest.raises(TimeoutError):
            await slow()


# ─── Fallback ───

class TestFallback:
    @pytest.mark.asyncio
    async def test_fallback_default(self):
        @fallback(default_value="default")
        async def broken():
            raise RuntimeError("boom")

        assert await broken() == "default"

    @pytest.mark.asyncio
    async def test_fallback_no_error(self):
        @fallback(default_value="default")
        async def good():
            return "real"

        assert await good() == "real"


# ─── Health Checker ───

class TestHealthChecker:
    @pytest.mark.asyncio
    async def test_health_check(self):
        checker = HealthChecker()
        checker.register("test", lambda: True)
        results = await checker.check_all()
        assert results["test"].healthy is True
        assert checker.is_healthy() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        checker = HealthChecker()
        checker.register("bad", lambda: False)
        await checker.check_all()
        assert checker.is_healthy() is False

    @pytest.mark.asyncio
    async def test_error_in_check(self):
        checker = HealthChecker()
        checker.register("err", lambda: 1/0)
        await checker.check_all()
        assert checker.is_healthy() is False
