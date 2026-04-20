"""心跳系统 — 定期健康检查 + 主动通知.

- 定期执行健康检查 (provider 连通性、内存、磁盘)
- 异常时主动通知 (回调 / webhook)
- 与 cron 区别: heartbeat 是固定间隔的健康探针，cron 是精确时间的任务调度

用法:
    hb = HeartbeatManager(interval=60)
    hb.add_check("provider", check_provider_health)
    hb.on_alert(my_alert_handler)
    await hb.start()
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

class HealthStatus(str, Enum):
    """健康状态."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

# 检查函数类型: async def check() -> HealthCheckResult
HealthCheckFn = Callable[[], Coroutine[Any, Any, "HealthCheckResult"]]
AlertHandler = Callable[["HeartbeatReport"], Coroutine[Any, Any, None]]

@dataclass
class HealthCheckResult:
    """单项检查结果."""

    name: str = ""
    status: HealthStatus = HealthStatus.UNKNOWN
    message: str = ""
    latency_ms: float = 0.0
    details: dict = field(default_factory=dict)

@dataclass
class HeartbeatReport:
    """心跳报告."""

    timestamp: float = 0.0
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    checks: list[HealthCheckResult] = field(default_factory=list)
    uptime_sec: float = 0.0
    consecutive_failures: int = 0

    @property
    def is_healthy(self) -> bool:
        return self.overall_status == HealthStatus.HEALTHY

class HeartbeatManager:
    """心跳管理器 — 定期健康检查 + 主动告警."""

    def __init__(self, interval: float = 60.0, failure_threshold: int = 3) -> None:
        self._interval = interval
        self._failure_threshold = failure_threshold
        self._checks: dict[str, HealthCheckFn] = {}
        self._alert_handlers: list[AlertHandler] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._start_time: float = 0.0
        self._consecutive_failures = 0
        self._last_report: Optional[HeartbeatReport] = None
        self._history: list[HeartbeatReport] = []
        self._max_history = 100

    def add_check(self, name: str, check_fn: HealthCheckFn) -> None:
        """注册健康检查项."""
        self._checks[name] = check_fn

    def remove_check(self, name: str) -> bool:
        """移除健康检查项."""
        return self._checks.pop(name, None) is not None

    def on_alert(self, handler: AlertHandler) -> None:
        """注册告警回调."""
        self._alert_handlers.append(handler)

    async def start(self) -> None:
        """启动心跳循环."""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._loop())
        logger.info("心跳已启动 (间隔 %.0fs, 阈值 %d)", self._interval, self._failure_threshold)

    async def stop(self) -> None:
        """停止心跳."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("心跳已停止")

    async def check_now(self) -> HeartbeatReport:
        """立即执行一次健康检查."""
        results = []
        for name, check_fn in self._checks.items():
            t0 = time.time()
            try:
                result = await asyncio.wait_for(check_fn(), timeout=30)
                result.name = name
                result.latency_ms = (time.time() - t0) * 1000
            except asyncio.TimeoutError:
                result = HealthCheckResult(
                    name=name, status=HealthStatus.UNHEALTHY,
                    message="检查超时 (30s)", latency_ms=30000,
                )
            except Exception as e:
                result = HealthCheckResult(
                    name=name, status=HealthStatus.UNHEALTHY,
                    message=str(e), latency_ms=(time.time() - t0) * 1000,
                )
            results.append(result)

        # 计算总体状态
        if not results:
            overall = HealthStatus.UNKNOWN
        elif all(r.status == HealthStatus.HEALTHY for r in results):
            overall = HealthStatus.HEALTHY
        elif any(r.status == HealthStatus.UNHEALTHY for r in results):
            overall = HealthStatus.UNHEALTHY
        else:
            overall = HealthStatus.DEGRADED

        # 连续失败计数
        if overall == HealthStatus.HEALTHY:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1

        report = HeartbeatReport(
            timestamp=time.time(),
            overall_status=overall,
            checks=results,
            uptime_sec=time.time() - self._start_time if self._start_time else 0,
            consecutive_failures=self._consecutive_failures,
        )

        self._last_report = report
        self._history.append(report)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # 触发告警
        if self._consecutive_failures >= self._failure_threshold:
            await self._fire_alerts(report)

        return report

    async def _loop(self) -> None:
        """心跳主循环."""
        while self._running:
            try:
                await self.check_now()
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("心跳循环异常: %s", e)
                await asyncio.sleep(self._interval)

    async def _fire_alerts(self, report: HeartbeatReport) -> None:
        """触发告警."""
        for handler in self._alert_handlers:
            try:
                await handler(report)
            except Exception as e:
                logger.error("告警处理失败: %s", e)

    def get_last_report(self) -> Optional[HeartbeatReport]:
        """获取最近一次报告."""
        return self._last_report

    def get_history(self, limit: int = 20) -> list[HeartbeatReport]:
        """获取历史报告."""
        return self._history[-limit:]

    @property
    def is_running(self) -> bool:
        return self._running
