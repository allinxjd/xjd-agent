"""Tests for gateway.core.monitoring — Prometheus 指标 + 告警."""

import pytest
from gateway.core.monitoring import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    AlertManager,
    AlertRule,
    AlertSeverity,
    Tracer,
)


class TestCounter:
    def test_inc(self):
        c = Counter("test_counter")
        c.inc()
        c.inc(5)
        assert c.get() == 6

    def test_labels(self):
        c = Counter("requests", labels=["method", "status"])
        c.inc(method="GET", status="200")
        c.inc(method="GET", status="200")
        c.inc(method="POST", status="201")
        assert c.get(method="GET", status="200") == 2
        assert c.get(method="POST", status="201") == 1

    def test_collect(self):
        c = Counter("c", labels=["a"])
        c.inc(a="x")
        data = c.collect()
        assert len(data) == 1
        assert data[0]["value"] == 1


class TestGauge:
    def test_set_get(self):
        g = Gauge("active")
        g.set(42)
        assert g.get() == 42

    def test_inc_dec(self):
        g = Gauge("g")
        g.inc(5)
        g.dec(2)
        assert g.get() == 3


class TestHistogram:
    def test_observe(self):
        h = Histogram("latency", buckets=(0.1, 0.5, 1.0, float("inf")))
        h.observe(0.05)
        h.observe(0.3)
        h.observe(0.8)
        data = h.collect()
        assert len(data) == 1
        assert data[0]["count"] == 3
        assert data[0]["sum"] == pytest.approx(1.15)

    def test_buckets(self):
        h = Histogram("h", buckets=(1, 5, 10, float("inf")))
        h.observe(3)
        h.observe(7)
        h.observe(0.5)
        data = h.collect()[0]
        assert data["buckets"][1] == 1   # <=1
        assert data["buckets"][5] == 2   # <=5
        assert data["buckets"][10] == 3  # <=10


class TestMetricsRegistry:
    def test_create_metrics(self):
        reg = MetricsRegistry()
        c = reg.counter("req_total", "Total requests")
        g = reg.gauge("active", "Active connections")
        h = reg.histogram("latency", "Latency")
        c.inc(10)
        g.set(5)
        h.observe(0.1)
        assert c.get() == 10
        assert g.get() == 5

    def test_prometheus_export(self):
        reg = MetricsRegistry()
        c = reg.counter("http_requests", "Total HTTP requests")
        c.inc(42)
        text = reg.export_prometheus()
        assert "http_requests" in text
        assert "42" in text
        assert "# TYPE http_requests counter" in text

    def test_get_all_metrics(self):
        reg = MetricsRegistry()
        reg.counter("c").inc()
        reg.gauge("g").set(1)
        all_m = reg.get_all_metrics()
        assert "c" in all_m
        assert "g" in all_m


class TestAlertManager:
    def test_add_rule(self):
        am = AlertManager()
        am.add_rule(AlertRule(rule_id="r1", name="High Error", threshold=0.5, condition="error_rate"))
        assert len(am._rules) == 1

    def test_fire_alert(self):
        am = AlertManager()
        fired_alerts = []
        am.on_alert(lambda a: fired_alerts.append(a))
        am.add_rule(AlertRule(
            rule_id="r1", name="High Error",
            threshold=0.5, condition="error_rate", duration=0,
        ))
        alerts = am.check_metric("error_rate", 0.8)
        assert len(alerts) == 1
        assert alerts[0].severity == AlertSeverity.WARNING
        assert len(fired_alerts) == 1

    def test_no_alert_below_threshold(self):
        am = AlertManager()
        am.add_rule(AlertRule(rule_id="r1", threshold=0.5, condition="error_rate", duration=0))
        alerts = am.check_metric("error_rate", 0.3)
        assert len(alerts) == 0


class TestTracer:
    def test_span(self):
        tracer = Tracer("test")
        with tracer.span("op1") as span:
            span.tags["key"] = "val"
        spans = tracer.get_recent_spans()
        assert len(spans) == 1
        assert spans[0].operation == "op1"
        assert spans[0].duration_ms >= 0  # 空操作可能 duration=0
        assert spans[0].tags["key"] == "val"

    def test_error_span(self):
        tracer = Tracer()
        try:
            with tracer.span("bad_op") as span:
                raise ValueError("oops")
        except ValueError:
            pass
        spans = tracer.get_recent_spans()
        assert spans[0].status == "error"
