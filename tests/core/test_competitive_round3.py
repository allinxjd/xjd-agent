"""测试 — 竞品对齐第三轮: Task Brain, Proactive Notifications, Allow/Deny Tools, Multi Memory Providers."""

from __future__ import annotations

import asyncio
import pytest


# ── Task Brain ──

class TestTaskBrain:
    @pytest.mark.asyncio
    async def test_submit_and_status(self):
        from agent.core.task_brain import TaskBrain, TaskStatus

        brain = TaskBrain()
        task_id = await brain.submit("测试任务", priority=3)
        status = brain.get_status(task_id)
        assert status is not None
        assert status.status == TaskStatus.PENDING
        assert status.description == "测试任务"
        assert status.priority == 3

    @pytest.mark.asyncio
    async def test_reject_blocked_keyword(self):
        from agent.core.task_brain import TaskBrain, TaskStatus, RejectReason

        brain = TaskBrain()
        brain.add_blocked_keywords(["危险操作"])
        task_id = await brain.submit("执行危险操作")
        status = brain.get_status(task_id)
        assert status.status == TaskStatus.REJECTED
        assert status.reject_reason == RejectReason.BLOCKED_KEYWORD

    @pytest.mark.asyncio
    async def test_cancel_task(self):
        from agent.core.task_brain import TaskBrain, TaskStatus

        brain = TaskBrain()
        task_id = await brain.submit("可取消任务")
        ok = await brain.cancel(task_id)
        assert ok is True
        assert brain.get_status(task_id).status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_queue_full_reject(self):
        from agent.core.task_brain import TaskBrain, TaskStatus, RejectReason

        brain = TaskBrain(max_queue_size=1)
        await brain.submit("任务1")
        task_id2 = await brain.submit("任务2")
        status = brain.get_status(task_id2)
        assert status.status == TaskStatus.REJECTED
        assert status.reject_reason == RejectReason.QUEUE_FULL

    @pytest.mark.asyncio
    async def test_worker_execution(self):
        from agent.core.task_brain import TaskBrain, TaskStatus

        brain = TaskBrain()
        results = []

        async def handler(desc, meta):
            results.append(desc)
            return f"done: {desc}"

        brain.set_handler(handler)
        task_id = await brain.submit("执行任务")
        await brain.start(num_workers=1)
        await asyncio.sleep(0.3)
        await brain.stop()

        status = brain.get_status(task_id)
        assert status.status == TaskStatus.COMPLETED
        assert "执行任务" in results

    @pytest.mark.asyncio
    async def test_list_tasks(self):
        from agent.core.task_brain import TaskBrain, TaskStatus

        brain = TaskBrain()
        await brain.submit("任务A", priority=5)
        await brain.submit("任务B", priority=1)
        tasks = brain.list_tasks()
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_custom_reject_policy(self):
        from agent.core.task_brain import TaskBrain, TaskStatus, RejectReason

        brain = TaskBrain()
        brain.add_reject_policy(lambda desc, meta: RejectReason.SECURITY if "sudo" in desc else None)
        task_id = await brain.submit("sudo rm -rf /")
        assert brain.get_status(task_id).status == TaskStatus.REJECTED
        assert brain.get_status(task_id).reject_reason == RejectReason.SECURITY

    @pytest.mark.asyncio
    async def test_stats(self):
        from agent.core.task_brain import TaskBrain

        brain = TaskBrain()
        await brain.submit("任务1")
        await brain.submit("任务2")
        stats = brain.get_stats()
        assert stats["total"] == 2
        assert stats["queue_size"] == 2


# ── Proactive Notifications ──

class TestProactiveNotifier:
    @pytest.mark.asyncio
    async def test_register_channel_and_send(self):
        from gateway.core.proactive import ProactiveNotifier, NotificationType

        notifier = ProactiveNotifier()
        sent_messages = []

        async def mock_send(channel, recipient, message):
            sent_messages.append((channel, recipient, message))
            return True

        notifier.register_channel("telegram", mock_send)
        notif = await notifier.send_direct("telegram", "user1", "你好")
        assert notif is not None
        assert notif.delivered is True
        assert len(sent_messages) == 1

    @pytest.mark.asyncio
    async def test_rule_based_notify(self):
        from gateway.core.proactive import ProactiveNotifier, NotificationRule

        notifier = ProactiveNotifier()
        sent = []

        async def mock_send(ch, rec, msg):
            sent.append(msg)
            return True

        notifier.register_channel("discord", mock_send)
        notifier.add_rule(NotificationRule(
            name="task_done",
            event="task.completed",
            channels=["discord"],
            template="任务 {task_name} 已完成",
        ))

        results = await notifier.notify("task.completed", {"task_name": "分析代码"})
        assert len(results) == 1
        assert "分析代码" in sent[0]

    @pytest.mark.asyncio
    async def test_cooldown(self):
        from gateway.core.proactive import ProactiveNotifier, NotificationRule

        notifier = ProactiveNotifier()
        count = 0

        async def mock_send(ch, rec, msg):
            nonlocal count
            count += 1
            return True

        notifier.register_channel("slack", mock_send)
        notifier.add_rule(NotificationRule(
            name="alert",
            event="error",
            channels=["slack"],
            template="Error!",
            cooldown_sec=60,
        ))

        await notifier.notify("error")
        await notifier.notify("error")  # should be cooled down
        assert count == 1

    @pytest.mark.asyncio
    async def test_remove_rule(self):
        from gateway.core.proactive import ProactiveNotifier, NotificationRule

        notifier = ProactiveNotifier()
        notifier.add_rule(NotificationRule(name="r1", event="e1", channels=[]))
        assert notifier.remove_rule("r1") is True
        assert notifier.remove_rule("nonexist") is False

    @pytest.mark.asyncio
    async def test_history_and_stats(self):
        from gateway.core.proactive import ProactiveNotifier

        notifier = ProactiveNotifier()

        async def mock_send(ch, rec, msg):
            return True

        notifier.register_channel("test", mock_send)
        await notifier.send_direct("test", "u1", "msg1")
        await notifier.send_direct("test", "u2", "msg2")

        history = notifier.get_history()
        assert len(history) == 2

        stats = notifier.get_stats()
        assert stats["total_sent"] == 2
        assert stats["delivered"] == 2


# ── Allow/Deny Tools ──

class TestToolAllowDeny:
    def test_allow_list(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("read_file", "读取文件", {}, lambda: None)
        reg.register("write_file", "写入文件", {}, lambda: None)
        reg.register("run_cmd", "执行命令", {}, lambda: None)

        count = reg.apply_allow_list(["read_file", "write_file"])
        assert count == 2
        assert reg.get("read_file").enabled is True
        assert reg.get("write_file").enabled is True
        assert reg.get("run_cmd").enabled is False

    def test_deny_list(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("read_file", "读取文件", {}, lambda: None)
        reg.register("run_cmd", "执行命令", {}, lambda: None)

        count = reg.apply_deny_list(["run_cmd"])
        assert count == 1
        assert reg.get("read_file").enabled is True
        assert reg.get("run_cmd").enabled is False

    def test_list_by_category(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("t1", "desc", {}, lambda: None, category="fs")
        reg.register("t2", "desc", {}, lambda: None, category="fs")
        reg.register("t3", "desc", {}, lambda: None, category="net")

        fs_tools = reg.list_by_category("fs")
        assert len(fs_tools) == 2

    def test_get_categories(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("t1", "d", {}, lambda: None, category="a")
        reg.register("t2", "d", {}, lambda: None, category="b")
        cats = reg.get_categories()
        assert cats == ["a", "b"]


# ── Multi Memory Providers ──

class TestMemoryProviderFactory:
    def test_create_sqlite_provider(self):
        from agent.memory.provider import create_memory_provider, BuiltinMemoryProvider

        p = create_memory_provider("sqlite")
        assert isinstance(p, BuiltinMemoryProvider)

    def test_create_redis_provider(self):
        from agent.memory.provider import create_memory_provider, RedisMemoryProvider

        p = create_memory_provider("redis", url="redis://localhost:6379")
        assert isinstance(p, RedisMemoryProvider)

    def test_create_postgresql_provider(self):
        from agent.memory.provider import create_memory_provider, PostgreSQLMemoryProvider

        p = create_memory_provider("postgresql", dsn="postgresql://localhost/test")
        assert isinstance(p, PostgreSQLMemoryProvider)

    def test_create_chromadb_provider(self):
        from agent.memory.provider import create_memory_provider, ChromaDBMemoryProvider

        p = create_memory_provider("chromadb")
        assert isinstance(p, ChromaDBMemoryProvider)

    def test_unknown_provider_raises(self):
        from agent.memory.provider import create_memory_provider

        with pytest.raises(ValueError, match="未知"):
            create_memory_provider("unknown_db")

    def test_provider_registry_keys(self):
        from agent.memory.provider import PROVIDER_REGISTRY

        assert "sqlite" in PROVIDER_REGISTRY
        assert "redis" in PROVIDER_REGISTRY
        assert "postgresql" in PROVIDER_REGISTRY
        assert "chromadb" in PROVIDER_REGISTRY
