"""监控告警 — Prometheus 指标 + 日志结构化 + 告警通知.

提供:
- Prometheus 风格指标 (Counter / Gauge / Histogram)
- 结构化日志
- 告警规则引擎
- 指标导出 (Prometheus text format)
- 性能追踪 (Tracing)
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════
#  指标类型
# ═══════════════════════════════════════════

class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    SUMMARY = "summary"

class Counter:
    """计数器 — 只增不减."""

    def __init__(self, name: str, help: str = "", labels: list[str] | None = None) -> None:
        self.name = name
        self.help = help
        self._labels = labels or []
        self._values: dict[tuple, float] = defaultdict(float)

    def inc(self, amount: float = 1.0, **label_values) -> None:
        key = self._make_key(label_values)
        self._values[key] += amount

    def get(self, **label_values) -> float:
        key = self._make_key(label_values)
        return self._values.get(key, 0.0)

    def _make_key(self, labels: dict) -> tuple:
        if not self._labels:
            return ()
        return tuple(labels.get(l, "") for l in self._labels)

    def collect(self) -> list[dict]:
        results = []
        for key, value in self._values.items():
            labels = dict(zip(self._labels, key)) if self._labels else {}
            results.append({"labels": labels, "value": value})
        return results

class Gauge:
    """仪表盘 — 可增可减."""

    def __init__(self, name: str, help: str = "", labels: list[str] | None = None) -> None:
        self.name = name
        self.help = help
        self._labels = labels or []
        self._values: dict[tuple, float] = defaultdict(float)

    def set(self, value: float, **label_values) -> None:
        key = self._make_key(label_values)
        self._values[key] = value

    def inc(self, amount: float = 1.0, **label_values) -> None:
        key = self._make_key(label_values)
        self._values[key] += amount

    def dec(self, amount: float = 1.0, **label_values) -> None:
        key = self._make_key(label_values)
        self._values[key] -= amount

    def get(self, **label_values) -> float:
        key = self._make_key(label_values)
        return self._values.get(key, 0.0)

    def _make_key(self, labels: dict) -> tuple:
        if not self._labels:
            return ()
        return tuple(labels.get(l, "") for l in self._labels)

    def collect(self) -> list[dict]:
        results = []
        for key, value in self._values.items():
            labels = dict(zip(self._labels, key)) if self._labels else {}
            results.append({"labels": labels, "value": value})
        return results

class Histogram:
    """直方图 — 统计值分布."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, float("inf"))

    def __init__(
        self,
        name: str,
        help: str = "",
        buckets: tuple | None = None,
        labels: list[str] | None = None,
    ) -> None:
        self.name = name
        self.help = help
        self._labels = labels or []
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._sums: dict[tuple, float] = defaultdict(float)
        self._counts: dict[tuple, int] = defaultdict(int)
        self._bucket_counts: dict[tuple, dict[float, int]] = {}

    def observe(self, value: float, **label_values) -> None:
        key = self._make_key(label_values)
        self._sums[key] += value
        self._counts[key] += 1

        if key not in self._bucket_counts:
            self._bucket_counts[key] = {b: 0 for b in self._buckets}

        for b in self._buckets:
            if value <= b:
                self._bucket_counts[key][b] += 1

    def _make_key(self, labels: dict) -> tuple:
        if not self._labels:
            return ()
        return tuple(labels.get(l, "") for l in self._labels)

    def collect(self) -> list[dict]:
        results = []
        for key in self._sums:
            labels = dict(zip(self._labels, key)) if self._labels else {}
            results.append({
                "labels": labels,
                "sum": self._sums[key],
                "count": self._counts[key],
                "buckets": dict(self._bucket_counts.get(key, {})),
            })
        return results

# ═══════════════════════════════════════════
#  指标注册表
# ═══════════════════════════════════════════

class MetricsRegistry:
    """指标注册表.

    用法:
        registry = MetricsRegistry()

        # 创建指标
        req_counter = registry.counter("http_requests_total", "Total requests", ["method", "status"])
        latency = registry.histogram("request_duration_seconds", "Request duration")
        active = registry.gauge("active_connections", "Active connections")

        # 记录
        req_counter.inc(method="POST", status="200")
        latency.observe(0.123)
        active.set(42)

        # 导出
        text = registry.export_prometheus()
    """

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    def counter(self, name: str, help: str = "", labels: list[str] | None = None) -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter(name, help, labels)
        return self._counters[name]

    def gauge(self, name: str, help: str = "", labels: list[str] | None = None) -> Gauge:
        if name not in self._gauges:
            self._gauges[name] = Gauge(name, help, labels)
        return self._gauges[name]

    def histogram(
        self,
        name: str,
        help: str = "",
        buckets: tuple | None = None,
        labels: list[str] | None = None,
    ) -> Histogram:
        if name not in self._histograms:
            self._histograms[name] = Histogram(name, help, buckets, labels)
        return self._histograms[name]

    def export_prometheus(self) -> str:
        """导出 Prometheus text format."""
        lines: list[str] = []

        for name, c in self._counters.items():
            if c.help:
                lines.append(f"# HELP {name} {c.help}")
            lines.append(f"# TYPE {name} counter")
            for item in c.collect():
                label_str = self._format_labels(item["labels"])
                lines.append(f"{name}{label_str} {item['value']}")

        for name, g in self._gauges.items():
            if g.help:
                lines.append(f"# HELP {name} {g.help}")
            lines.append(f"# TYPE {name} gauge")
            for item in g.collect():
                label_str = self._format_labels(item["labels"])
                lines.append(f"{name}{label_str} {item['value']}")

        for name, h in self._histograms.items():
            if h.help:
                lines.append(f"# HELP {name} {h.help}")
            lines.append(f"# TYPE {name} histogram")
            for item in h.collect():
                label_str = self._format_labels(item["labels"])
                for bucket, count in sorted(item.get("buckets", {}).items()):
                    le = "+Inf" if bucket == float("inf") else str(bucket)
                    extra_label = f',le="{le}"' if label_str else f'le="{le}"'
                    if label_str:
                        full_label = label_str[:-1] + extra_label + "}"
                    else:
                        full_label = "{" + extra_label + "}"
                    lines.append(f"{name}_bucket{full_label} {count}")
                lines.append(f"{name}_sum{label_str} {item['sum']}")
                lines.append(f"{name}_count{label_str} {item['count']}")

        return "\n".join(lines)

    def _format_labels(self, labels: dict) -> str:
        if not labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in labels.items()]
        return "{" + ",".join(parts) + "}"

    def get_all_metrics(self) -> dict[str, Any]:
        """获取所有指标 (dict 格式)."""
        result: dict[str, Any] = {}
        for name, c in self._counters.items():
            result[name] = {"type": "counter", "data": c.collect()}
        for name, g in self._gauges.items():
            result[name] = {"type": "gauge", "data": g.collect()}
        for name, h in self._histograms.items():
            result[name] = {"type": "histogram", "data": h.collect()}
        return result

# 全局指标实例
metrics = MetricsRegistry()

# 预定义指标
request_counter = metrics.counter(
    "xjd_requests_total", "Total requests", ["type", "status"]
)
request_duration = metrics.histogram(
    "xjd_request_duration_seconds", "Request duration", labels=["type"]
)
active_sessions = metrics.gauge(
    "xjd_active_sessions", "Active sessions"
)
token_usage = metrics.counter(
    "xjd_tokens_total", "Total tokens used", ["model", "type"]
)
tool_calls = metrics.counter(
    "xjd_tool_calls_total", "Total tool calls", ["tool", "status"]
)
errors_counter = metrics.counter(
    "xjd_errors_total", "Total errors", ["component", "type"]
)

# ═══════════════════════════════════════════
#  告警规则
# ═══════════════════════════════════════════

class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

@dataclass
class AlertRule:
    """告警规则."""

    rule_id: str = ""
    name: str = ""
    description: str = ""
    severity: AlertSeverity = AlertSeverity.WARNING
    condition: str = ""        # 条件表达式 (简化版)
    threshold: float = 0.0
    duration: int = 60         # 持续多久才触发 (秒)
    enabled: bool = True

    # 通知
    notify_channels: list[str] = field(default_factory=list)

@dataclass
class Alert:
    """告警实例."""

    alert_id: str = ""
    rule_id: str = ""
    severity: AlertSeverity = AlertSeverity.WARNING
    message: str = ""
    value: float = 0.0
    fired_at: float = 0.0
    resolved_at: float = 0.0
    acknowledged: bool = False

class AlertManager:
    """告警管理器.

    用法:
        am = AlertManager()

        # 添加规则
        am.add_rule(AlertRule(
            rule_id="high_error_rate",
            name="错误率过高",
            severity=AlertSeverity.CRITICAL,
            threshold=0.1,
        ))

        # 注册通知渠道
        am.on_alert(print_alert)

        # 检查告警
        am.check_metric("error_rate", 0.15)
    """

    def __init__(self) -> None:
        self._rules: dict[str, AlertRule] = {}
        self._active_alerts: dict[str, Alert] = {}
        self._alert_history: list[Alert] = []
        self._callbacks: list[Callable] = []
        self._metric_history: dict[str, list[tuple[float, float]]] = {}  # metric → [(time, value)]

    def add_rule(self, rule: AlertRule) -> None:
        self._rules[rule.rule_id] = rule

    def on_alert(self, callback: Callable) -> None:
        self._callbacks.append(callback)

    def check_metric(self, metric_name: str, value: float) -> list[Alert]:
        """检查指标是否触发告警."""
        now = time.time()

        # 记录指标历史
        if metric_name not in self._metric_history:
            self._metric_history[metric_name] = []
        self._metric_history[metric_name].append((now, value))

        # 清理旧数据 (保留 1 小时)
        cutoff = now - 3600
        self._metric_history[metric_name] = [
            (t, v) for t, v in self._metric_history[metric_name] if t > cutoff
        ]

        fired: list[Alert] = []
        for rule in self._rules.values():
            if not rule.enabled:
                continue
            if rule.condition and rule.condition != metric_name:
                continue

            if value > rule.threshold:
                # 检查是否持续超过 duration
                history = self._metric_history.get(metric_name, [])
                duration_start = now - rule.duration
                recent = [v for t, v in history if t >= duration_start]

                if all(v > rule.threshold for v in recent) and len(recent) > 0:
                    alert_key = f"{rule.rule_id}:{metric_name}"
                    if alert_key not in self._active_alerts:
                        alert = Alert(
                            alert_id=f"alert_{int(now)}",
                            rule_id=rule.rule_id,
                            severity=rule.severity,
                            message=f"{rule.name}: {metric_name}={value:.4f} > {rule.threshold}",
                            value=value,
                            fired_at=now,
                        )
                        self._active_alerts[alert_key] = alert
                        self._alert_history.append(alert)
                        fired.append(alert)

                        # 通知
                        for cb in self._callbacks:
                            try:
                                cb(alert)
                            except Exception as e:
                                logger.error("Alert callback error: %s", e)
            else:
                # 解决告警
                alert_key = f"{rule.rule_id}:{metric_name}"
                if alert_key in self._active_alerts:
                    self._active_alerts[alert_key].resolved_at = now
                    del self._active_alerts[alert_key]

        return fired

    def get_active_alerts(self) -> list[Alert]:
        return list(self._active_alerts.values())

    def get_alert_history(self, limit: int = 100) -> list[Alert]:
        return self._alert_history[-limit:]

    def acknowledge(self, alert_id: str) -> bool:
        for alert in self._active_alerts.values():
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                return True
        return False

# ═══════════════════════════════════════════
#  性能追踪
# ═══════════════════════════════════════════

@dataclass
class Span:
    """追踪 Span."""

    trace_id: str = ""
    span_id: str = ""
    parent_id: str = ""
    operation: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    tags: dict[str, str] = field(default_factory=dict)
    logs: list[dict] = field(default_factory=list)
    status: str = "ok"

class Tracer:
    """性能追踪器.

    用法:
        tracer = Tracer()

        with tracer.span("process_message") as span:
            span.tags["user_id"] = "123"
            # 执行操作
            result = await process()
            span.logs.append({"event": "processed", "result_len": len(result)})
    """

    def __init__(self, service_name: str = "xjd-agent") -> None:
        self._service_name = service_name
        self._spans: list[Span] = []
        self._max_spans = 1000

    def span(self, operation: str, parent_id: str = "") -> SpanContext:
        return SpanContext(self, operation, parent_id)

    def _record_span(self, span: Span) -> None:
        self._spans.append(span)
        if len(self._spans) > self._max_spans:
            self._spans = self._spans[-self._max_spans:]

    def get_recent_spans(self, limit: int = 50) -> list[Span]:
        return self._spans[-limit:]

    def get_slow_spans(self, threshold_ms: float = 1000) -> list[Span]:
        return [s for s in self._spans if s.duration_ms > threshold_ms]

class SpanContext:
    """Span 上下文管理器."""

    def __init__(self, tracer: Tracer, operation: str, parent_id: str = "") -> None:
        self._tracer = tracer
        import secrets
        self._span = Span(
            trace_id=secrets.token_hex(8),
            span_id=secrets.token_hex(4),
            parent_id=parent_id,
            operation=operation,
        )

    def __enter__(self) -> Span:
        self._span.start_time = time.time()
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._span.end_time = time.time()
        self._span.duration_ms = (self._span.end_time - self._span.start_time) * 1000
        if exc_type:
            self._span.status = "error"
            self._span.tags["error"] = str(exc_val)
        self._tracer._record_span(self._span)
