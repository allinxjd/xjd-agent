"""生产级加固 — 重试/断路器/限流/错误恢复.

提供:
- 指数退避重试 (Exponential Backoff Retry)
- 断路器 (Circuit Breaker)
- 限流器 (Token Bucket + Sliding Window)
- 超时控制 (Timeout)
- 优雅降级 (Graceful Degradation)
- 健康检查 (Health Check)
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ═══════════════════════════════════════════
#  重试装饰器
# ═══════════════════════════════════════════

@dataclass
class RetryConfig:
    """重试配置."""

    max_retries: int = 3
    base_delay: float = 1.0       # 基础延迟 (秒)
    max_delay: float = 60.0       # 最大延迟
    exponential_base: float = 2.0
    jitter: bool = True           # 添加随机抖动
    retry_on: tuple = (Exception,)  # 需要重试的异常类型
    on_retry: Optional[Callable] = None  # 重试回调

def retry(config: RetryConfig | None = None, **kwargs):
    """异步重试装饰器.

    用法:
        @retry(max_retries=3, base_delay=1.0)
        async def call_api():
            ...

        @retry(RetryConfig(max_retries=5, retry_on=(httpx.HTTPError,)))
        async def robust_call():
            ...
    """
    if config is None:
        config = RetryConfig(**kwargs)

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kw):
            last_error = None
            for attempt in range(config.max_retries + 1):
                try:
                    return await func(*args, **kw)
                except config.retry_on as e:
                    last_error = e
                    if attempt >= config.max_retries:
                        break

                    delay = min(
                        config.base_delay * (config.exponential_base ** attempt),
                        config.max_delay,
                    )
                    if config.jitter:
                        delay *= (0.5 + random.random())

                    logger.warning(
                        "Retry %d/%d for %s: %s (delay=%.1fs)",
                        attempt + 1, config.max_retries,
                        func.__name__, e, delay,
                    )

                    if config.on_retry:
                        try:
                            config.on_retry(attempt + 1, e)
                        except Exception:
                            pass

                    await asyncio.sleep(delay)

            raise last_error  # type: ignore

        return wrapper
    return decorator

# ═══════════════════════════════════════════
#  断路器
# ═══════════════════════════════════════════

class CircuitState(str, Enum):
    CLOSED = "closed"       # 正常
    OPEN = "open"           # 熔断
    HALF_OPEN = "half_open"  # 半开 (试探)

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5     # 连续失败次数触发熔断
    recovery_timeout: float = 30.0  # 熔断恢复时间 (秒)
    half_open_max_calls: int = 1   # 半开状态最大调用数
    success_threshold: int = 2     # 半开状态连续成功次数 → 关闭
    excluded_exceptions: tuple = ()  # 不计入失败的异常

class CircuitBreaker:
    """断路器.

    用法:
        cb = CircuitBreaker("openai_api")

        @cb
        async def call_openai():
            ...

        # 或手动
        if cb.allow_request():
            try:
                result = await call_api()
                cb.record_success()
            except Exception as e:
                cb.record_failure()
                raise
    """

    def __init__(
        self,
        name: str = "default",
        config: CircuitBreakerConfig | None = None,
    ) -> None:
        self.name = name
        self._config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time > self._config.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                self._success_count = 0
                logger.info("CircuitBreaker[%s]: OPEN → HALF_OPEN", self.name)
        return self._state

    def allow_request(self) -> bool:
        state = self.state
        if state == CircuitState.CLOSED:
            return True
        elif state == CircuitState.HALF_OPEN:
            return self._half_open_calls < self._config.half_open_max_calls
        else:  # OPEN
            return False

    def record_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._config.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                logger.info("CircuitBreaker[%s]: HALF_OPEN → CLOSED", self.name)
        else:
            self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.warning("CircuitBreaker[%s]: HALF_OPEN → OPEN", self.name)
        elif self._failure_count >= self._config.failure_threshold:
            self._state = CircuitState.OPEN
            logger.warning(
                "CircuitBreaker[%s]: CLOSED → OPEN (failures=%d)",
                self.name, self._failure_count,
            )

    def __call__(self, func):
        """作为装饰器使用."""
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not self.allow_request():
                raise CircuitOpenError(
                    f"Circuit breaker [{self.name}] is OPEN"
                )

            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1

            try:
                result = await func(*args, **kwargs)
                self.record_success()
                return result
            except self._config.excluded_exceptions:
                raise
            except Exception as e:
                self.record_failure()
                raise

        return wrapper

    def get_stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
        }

class CircuitOpenError(Exception):
    """断路器打开时的异常."""
    pass

# ═══════════════════════════════════════════
#  令牌桶限流
# ═══════════════════════════════════════════

class TokenBucket:
    """令牌桶限流器.

    用法:
        bucket = TokenBucket(rate=10, capacity=20)  # 每秒10个, 桶容量20

        if bucket.consume():
            # 允许请求
            ...
        else:
            # 限流
            ...
    """

    def __init__(self, rate: float, capacity: int) -> None:
        self._rate = rate        # 每秒生成的令牌数
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.time()

    def _refill(self) -> None:
        now = time.time()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate,
        )
        self._last_refill = now

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def wait_time(self) -> float:
        """等多久可以获取下一个令牌."""
        self._refill()
        if self._tokens >= 1:
            return 0.0
        return (1 - self._tokens) / self._rate

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens

# ═══════════════════════════════════════════
#  超时控制
# ═══════════════════════════════════════════

def timeout(seconds: float):
    """异步超时装饰器.

    用法:
        @timeout(30)
        async def slow_operation():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(
                    func(*args, **kwargs),
                    timeout=seconds,
                )
            except asyncio.TimeoutError:
                raise TimeoutError(
                    f"{func.__name__} timed out after {seconds}s"
                )
        return wrapper
    return decorator

# ═══════════════════════════════════════════
#  优雅降级
# ═══════════════════════════════════════════

def fallback(default_value=None, fallback_func=None):
    """降级装饰器 — 异常时返回默认值或调用备用函数.

    用法:
        @fallback(default_value="服务暂时不可用")
        async def risky_call():
            ...

        @fallback(fallback_func=cached_result)
        async def api_call():
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.warning(
                    "Fallback triggered for %s: %s",
                    func.__name__, e,
                )
                if fallback_func:
                    try:
                        if asyncio.iscoroutinefunction(fallback_func):
                            return await fallback_func(*args, **kwargs)
                        return fallback_func(*args, **kwargs)
                    except Exception as fe:
                        logger.error("Fallback function also failed: %s", fe)
                return default_value
        return wrapper
    return decorator

# ═══════════════════════════════════════════
#  健康检查
# ═══════════════════════════════════════════

@dataclass
class HealthStatus:
    """组件健康状态."""

    name: str = ""
    healthy: bool = True
    message: str = ""
    latency_ms: float = 0.0
    last_check: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

class HealthChecker:
    """健康检查管理器.

    用法:
        health = HealthChecker()
        health.register("redis", redis_check_func)
        health.register("db", db_check_func)

        status = await health.check_all()
    """

    def __init__(self) -> None:
        self._checks: dict[str, Callable] = {}
        self._last_results: dict[str, HealthStatus] = {}

    def register(self, name: str, check_func: Callable) -> None:
        """注册健康检查函数."""
        self._checks[name] = check_func

    async def check(self, name: str) -> HealthStatus:
        """执行单个健康检查."""
        check_func = self._checks.get(name)
        if not check_func:
            return HealthStatus(name=name, healthy=False, message="Unknown check")

        start = time.time()
        try:
            if asyncio.iscoroutinefunction(check_func):
                result = await asyncio.wait_for(check_func(), timeout=10.0)
            else:
                result = check_func()

            latency = (time.time() - start) * 1000
            status = HealthStatus(
                name=name,
                healthy=bool(result),
                message="OK" if result else "Unhealthy",
                latency_ms=round(latency, 2),
                last_check=time.time(),
            )

            if isinstance(result, dict):
                status.details = result

        except asyncio.TimeoutError:
            status = HealthStatus(
                name=name,
                healthy=False,
                message="Health check timed out",
                latency_ms=10000,
                last_check=time.time(),
            )
        except Exception as e:
            latency = (time.time() - start) * 1000
            status = HealthStatus(
                name=name,
                healthy=False,
                message=str(e),
                latency_ms=round(latency, 2),
                last_check=time.time(),
            )

        self._last_results[name] = status
        return status

    async def check_all(self) -> dict[str, HealthStatus]:
        """执行所有健康检查."""
        results = {}
        for name in self._checks:
            results[name] = await self.check(name)
        return results

    def is_healthy(self) -> bool:
        """整体是否健康."""
        if not self._last_results:
            return True
        return all(s.healthy for s in self._last_results.values())

    def get_summary(self) -> dict[str, Any]:
        return {
            "overall": "healthy" if self.is_healthy() else "unhealthy",
            "checks": {
                name: {
                    "healthy": s.healthy,
                    "message": s.message,
                    "latency_ms": s.latency_ms,
                }
                for name, s in self._last_results.items()
            },
        }

# ═══════════════════════════════════════════
#  Bulkhead (舱壁隔离)
# ═══════════════════════════════════════════

class Bulkhead:
    """舱壁隔离 — 限制并发调用数.

    用法:
        bulkhead = Bulkhead(max_concurrent=5, max_wait=10)

        @bulkhead
        async def limited_api_call():
            ...
    """

    def __init__(self, max_concurrent: int = 10, max_wait: float = 30.0) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_wait = max_wait
        self._active = 0
        self._rejected = 0

    def __call__(self, func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                acquired = await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self._max_wait,
                )
            except asyncio.TimeoutError:
                self._rejected += 1
                raise BulkheadFullError(
                    f"Bulkhead full: {self._active} active calls"
                )

            self._active += 1
            try:
                return await func(*args, **kwargs)
            finally:
                self._active -= 1
                self._semaphore.release()

        return wrapper

    @property
    def stats(self) -> dict[str, int]:
        return {
            "active": self._active,
            "rejected": self._rejected,
        }

class BulkheadFullError(Exception):
    """舱壁已满异常."""
    pass
