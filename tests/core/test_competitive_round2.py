"""测试 — 竞品对齐第二轮: Cost Tracker, Hooks, Heartbeat, Wake Word, RAG, Browser Stealth."""

from __future__ import annotations

import pytest


class TestCostTracker:
    @pytest.mark.asyncio
    async def test_record(self, tmp_path):
        from agent.core.cost_tracker import CostTracker

        tracker = CostTracker(data_dir=tmp_path / "costs")
        await tracker.initialize()

        rec = tracker.record(
            provider="openai", model="gpt-4o",
            input_tokens=1000, output_tokens=500,
            input_price_per_mtok=2.5, output_price_per_mtok=10.0,
        )
        assert rec.total_tokens == 1500
        assert rec.total_cost > 0

    @pytest.mark.asyncio
    async def test_session_summary(self, tmp_path):
        from agent.core.cost_tracker import CostTracker

        tracker = CostTracker(data_dir=tmp_path / "costs")
        await tracker.initialize()

        tracker.record("openai", "gpt-4o", 1000, 500, 2.5, 10.0)
        tracker.record("deepseek", "deepseek-chat", 2000, 1000, 0.14, 0.28)

        summary = tracker.get_session_summary()
        assert summary.total_requests == 2
        assert summary.total_cost > 0
        assert "openai" in summary.by_provider
        assert "deepseek" in summary.by_provider

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        from agent.core.cost_tracker import CostTracker

        tracker = CostTracker(data_dir=tmp_path / "costs")
        await tracker.initialize()
        tracker.record("openai", "gpt-4o", 100, 50, 2.5, 10.0)

        # 新实例读取
        tracker2 = CostTracker(data_dir=tmp_path / "costs")
        await tracker2.initialize()
        summary = await tracker2.get_summary(days=1)
        assert summary.total_requests == 1

    @pytest.mark.asyncio
    async def test_budget_alert(self, tmp_path):
        from agent.core.cost_tracker import CostTracker

        alerts = []
        tracker = CostTracker(
            data_dir=tmp_path / "costs",
            budget_limit=0.001,
            on_budget_alert=lambda cost, limit: alerts.append((cost, limit)),
        )
        await tracker.initialize()
        tracker.record("openai", "gpt-4o", 10000, 5000, 2.5, 10.0)
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_export_csv(self, tmp_path):
        from agent.core.cost_tracker import CostTracker

        tracker = CostTracker(data_dir=tmp_path / "costs")
        await tracker.initialize()
        tracker.record("openai", "gpt-4o", 100, 50, 2.5, 10.0)

        csv = await tracker.export_csv(days=1)
        assert "timestamp" in csv
        assert "openai" in csv


class TestHookManager:
    @pytest.mark.asyncio
    async def test_register_and_trigger(self):
        from agent.core.hooks import HookManager, HookPhase

        hm = HookManager()
        results = []

        async def handler(event, data):
            results.append(event)
            return data

        hm.register("on_message", handler, phase=HookPhase.AFTER)
        await hm.trigger("on_message", {"text": "hello"})
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_hook_priority(self):
        from agent.core.hooks import HookManager, HookPhase

        hm = HookManager()
        order = []

        async def first(event, data):
            order.append("first")
            return data

        async def second(event, data):
            order.append("second")
            return data

        hm.register("test", second, priority=10, name="second")
        hm.register("test", first, priority=1, name="first")
        await hm.trigger("test", {})
        assert order == ["first", "second"]

    @pytest.mark.asyncio
    async def test_hook_data_modification(self):
        from agent.core.hooks import HookManager

        hm = HookManager()

        async def add_field(event, data):
            data["injected"] = True
            return data

        hm.register("test", add_field)
        result = await hm.trigger("test", {"original": True})
        assert result["injected"] is True
        assert result["original"] is True

    @pytest.mark.asyncio
    async def test_unregister(self):
        from agent.core.hooks import HookManager

        hm = HookManager()

        async def handler(event, data):
            return data

        hm.register("test", handler, name="my_hook")
        assert hm.unregister("test", "my_hook") is True
        assert hm.unregister("test", "nonexistent") is False

    @pytest.mark.asyncio
    async def test_webhook_handling(self):
        from agent.core.hooks import HookManager, WebhookConfig

        hm = HookManager()
        triggered = []

        async def on_webhook(event, data):
            triggered.append(data)
            return data

        hm.register("on_webhook", on_webhook)
        hm.register_webhook("/webhook/test", WebhookConfig(events=["on_webhook"]))

        result = await hm.handle_webhook("/webhook/test", {"action": "push"})
        assert result["status"] == "ok"
        assert len(triggered) == 1

    def test_list_hooks(self):
        from agent.core.hooks import HookManager

        hm = HookManager()

        async def handler(event, data):
            return data

        hm.register("test", handler, name="h1")
        hooks = hm.list_hooks()
        assert len(hooks) == 1
        assert hooks[0]["name"] == "h1"


class TestHeartbeatManager:
    @pytest.mark.asyncio
    async def test_check_now(self):
        from gateway.core.heartbeat import HeartbeatManager, HealthCheckResult, HealthStatus

        hb = HeartbeatManager()

        async def healthy_check():
            return HealthCheckResult(status=HealthStatus.HEALTHY, message="ok")

        hb.add_check("test", healthy_check)
        report = await hb.check_now()
        assert report.is_healthy
        assert len(report.checks) == 1

    @pytest.mark.asyncio
    async def test_unhealthy_detection(self):
        from gateway.core.heartbeat import HeartbeatManager, HealthCheckResult, HealthStatus

        hb = HeartbeatManager(failure_threshold=2)

        async def bad_check():
            return HealthCheckResult(status=HealthStatus.UNHEALTHY, message="down")

        hb.add_check("test", bad_check)
        r1 = await hb.check_now()
        assert not r1.is_healthy
        assert r1.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_alert_on_threshold(self):
        from gateway.core.heartbeat import HeartbeatManager, HealthCheckResult, HealthStatus

        alerts = []

        async def alert_handler(report):
            alerts.append(report)

        hb = HeartbeatManager(failure_threshold=2)
        hb.on_alert(alert_handler)

        async def bad_check():
            return HealthCheckResult(status=HealthStatus.UNHEALTHY, message="fail")

        hb.add_check("test", bad_check)
        await hb.check_now()  # failure 1
        await hb.check_now()  # failure 2 → alert
        assert len(alerts) == 1

    @pytest.mark.asyncio
    async def test_remove_check(self):
        from gateway.core.heartbeat import HeartbeatManager, HealthCheckResult, HealthStatus

        hb = HeartbeatManager()

        async def check():
            return HealthCheckResult(status=HealthStatus.HEALTHY)

        hb.add_check("test", check)
        assert hb.remove_check("test") is True
        assert hb.remove_check("nonexistent") is False

    @pytest.mark.asyncio
    async def test_history(self):
        from gateway.core.heartbeat import HeartbeatManager, HealthCheckResult, HealthStatus

        hb = HeartbeatManager()

        async def check():
            return HealthCheckResult(status=HealthStatus.HEALTHY)

        hb.add_check("test", check)
        await hb.check_now()
        await hb.check_now()
        assert len(hb.get_history()) == 2


class TestWakeWordDetector:
    def test_detect_in_text(self):
        from gateway.voice.wake_word import WakeWordDetector

        detector = WakeWordDetector(keywords=["小巨蛋", "hey egg"])
        event = detector.detect_in_text("你好小巨蛋，帮我查天气")
        assert event is not None
        assert event.keyword == "小巨蛋"
        assert event.confidence == 1.0

    def test_no_detection(self):
        from gateway.voice.wake_word import WakeWordDetector

        detector = WakeWordDetector(keywords=["小巨蛋"])
        event = detector.detect_in_text("今天天气不错")
        assert event is None

    def test_add_remove_keyword(self):
        from gateway.voice.wake_word import WakeWordDetector

        detector = WakeWordDetector(keywords=["小巨蛋"])
        detector.add_keyword("hello")
        assert "hello" in detector.keywords

        assert detector.remove_keyword("hello") is True
        assert "hello" not in detector.keywords

    def test_detection_count(self):
        from gateway.voice.wake_word import WakeWordDetector

        detector = WakeWordDetector(keywords=["test"])
        detector.detect_in_text("test one")
        detector.detect_in_text("test two")
        detector.detect_in_text("no match")
        assert detector.detection_count == 2

    def test_case_insensitive(self):
        from gateway.voice.wake_word import WakeWordDetector

        detector = WakeWordDetector(keywords=["Hey Egg"])
        event = detector.detect_in_text("HEY EGG what's up")
        assert event is not None


class TestRAGPipeline:
    @pytest.mark.asyncio
    async def test_ingest_text(self, tmp_path):
        from agent.core.rag import RAGPipeline

        rag = RAGPipeline(data_dir=tmp_path / "rag")
        await rag.initialize()

        count = await rag.ingest_text("Python 是一种编程语言，广泛用于数据科学和人工智能。")
        assert count >= 1

    @pytest.mark.asyncio
    async def test_ingest_file(self, tmp_path):
        from agent.core.rag import RAGPipeline

        (tmp_path / "test.txt").write_text("这是测试文档的内容。\n\n第二段内容。")
        rag = RAGPipeline(data_dir=tmp_path / "rag")
        await rag.initialize()

        count = await rag.ingest_file(tmp_path / "test.txt")
        assert count >= 1

    @pytest.mark.asyncio
    async def test_retrieve(self, tmp_path):
        from agent.core.rag import RAGPipeline

        rag = RAGPipeline(data_dir=tmp_path / "rag", score_threshold=0.0)
        await rag.initialize()

        await rag.ingest_text("Python 编程语言用于机器学习", source="doc1")
        await rag.ingest_text("JavaScript 用于前端开发", source="doc2")

        results = await rag.retrieve("Python 机器学习")
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_query_format(self, tmp_path):
        from agent.core.rag import RAGPipeline

        rag = RAGPipeline(data_dir=tmp_path / "rag", score_threshold=0.0)
        await rag.initialize()
        await rag.ingest_text("测试内容", source="test")

        output = await rag.query("测试")
        assert "检索到的相关内容" in output

    @pytest.mark.asyncio
    async def test_get_stats(self, tmp_path):
        from agent.core.rag import RAGPipeline

        rag = RAGPipeline(data_dir=tmp_path / "rag")
        await rag.initialize()
        await rag.ingest_text("内容一", source="a")
        await rag.ingest_text("内容二", source="b")

        stats = rag.get_stats()
        assert stats["total_chunks"] >= 2
        assert stats["total_sources"] == 2

    @pytest.mark.asyncio
    async def test_clear(self, tmp_path):
        from agent.core.rag import RAGPipeline

        rag = RAGPipeline(data_dir=tmp_path / "rag")
        await rag.initialize()
        await rag.ingest_text("内容")
        rag.clear()
        assert rag.get_stats()["total_chunks"] == 0

    @pytest.mark.asyncio
    async def test_ingest_directory(self, tmp_path):
        from agent.core.rag import RAGPipeline

        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.txt").write_text("文档A内容")
        (tmp_path / "docs" / "b.md").write_text("文档B内容")

        rag = RAGPipeline(data_dir=tmp_path / "rag")
        await rag.initialize()
        count = await rag.ingest_directory(tmp_path / "docs")
        assert count >= 2


class TestDocumentLoader:
    @pytest.mark.asyncio
    async def test_load_file(self, tmp_path):
        from agent.core.rag import DocumentLoader

        (tmp_path / "test.txt").write_text("hello world")
        doc = await DocumentLoader.load_file(tmp_path / "test.txt")
        assert doc.content == "hello world"
        assert doc.metadata["extension"] == ".txt"

    @pytest.mark.asyncio
    async def test_load_directory(self, tmp_path):
        from agent.core.rag import DocumentLoader

        (tmp_path / "a.py").write_text("print(1)")
        (tmp_path / "b.txt").write_text("hello")
        (tmp_path / "c.bin").write_bytes(b"\x00\x01")  # 不支持的格式

        docs = await DocumentLoader.load_directory(tmp_path)
        assert len(docs) == 2


class TestTextChunker:
    def test_fixed_chunking(self):
        from agent.core.rag import TextChunker, Document, ChunkStrategy

        chunker = TextChunker(strategy=ChunkStrategy.FIXED, chunk_size=20, overlap=5)
        doc = Document(content="a" * 50, source="test")
        chunks = chunker.chunk(doc)
        assert len(chunks) >= 2

    def test_paragraph_chunking(self):
        from agent.core.rag import TextChunker, Document, ChunkStrategy

        chunker = TextChunker(strategy=ChunkStrategy.PARAGRAPH, chunk_size=100)
        doc = Document(content="段落一内容。\n\n段落二内容。\n\n段落三内容。", source="test")
        chunks = chunker.chunk(doc)
        assert len(chunks) >= 1


class TestHomeAssistantPlugin:
    def test_get_tools(self):
        from agent.plugins.examples.homeassistant_plugin import HomeAssistantPlugin

        plugin = HomeAssistantPlugin(ha_url="http://localhost:8123", token="test")
        tools = plugin.get_tools()
        assert len(tools) == 5
        names = [t["name"] for t in tools]
        assert "ha_get_states" in names
        assert "ha_call_service" in names
        assert "ha_activate_scene" in names
