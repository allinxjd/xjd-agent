"""测试 — 持久化审计日志."""

from __future__ import annotations

import pytest


class TestAuditLogger:
    @pytest.mark.asyncio
    async def test_log_and_query(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit = AuditLogger(log_dir=tmp_path / "audit")
        await audit.initialize()

        await audit.log("tool_call", user="admin", detail="web_search")
        await audit.log("chat", user="user1", detail="hello")

        entries = await audit.query()
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_query_by_action(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit = AuditLogger(log_dir=tmp_path / "audit")
        await audit.initialize()

        await audit.log("tool_call", user="admin")
        await audit.log("chat", user="user1")
        await audit.log("tool_call", user="admin")

        entries = await audit.query(action="tool_call")
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_query_by_user(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit = AuditLogger(log_dir=tmp_path / "audit")
        await audit.initialize()

        await audit.log("chat", user="alice")
        await audit.log("chat", user="bob")

        entries = await audit.query(user="alice")
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_integrity_verification(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit = AuditLogger(log_dir=tmp_path / "audit")
        await audit.initialize()

        await audit.log("action1")
        await audit.log("action2")
        await audit.log("action3")

        valid, total = await audit.verify_integrity()
        assert valid is True
        assert total == 3

    @pytest.mark.asyncio
    async def test_get_stats(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit = AuditLogger(log_dir=tmp_path / "audit")
        await audit.initialize()

        await audit.log("tool_call")
        await audit.log("tool_call")
        await audit.log("chat")

        stats = await audit.get_stats()
        assert stats["total"] == 3
        assert stats["by_action"]["tool_call"] == 2

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit_dir = tmp_path / "audit"

        # 写入
        audit1 = AuditLogger(log_dir=audit_dir)
        await audit1.initialize()
        await audit1.log("test_action", detail="persistent")

        # 新实例读取
        audit2 = AuditLogger(log_dir=audit_dir)
        await audit2.initialize()
        entries = await audit2.query()
        assert len(entries) == 1
        assert entries[0].detail == "persistent"

    @pytest.mark.asyncio
    async def test_hash_chain(self, tmp_path):
        from agent.core.audit import AuditLogger

        audit = AuditLogger(log_dir=tmp_path / "audit")
        await audit.initialize()

        e1 = await audit.log("a")
        e2 = await audit.log("b")
        assert e1.integrity_hash != e2.integrity_hash
        assert len(e1.integrity_hash) == 64
