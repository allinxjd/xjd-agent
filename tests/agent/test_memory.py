"""测试 — Memory 记忆系统."""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.memory.provider import (
    BuiltinMemoryProvider,
    Memory,
    MemoryImportance,
    MemoryType,
)


class TestMemory:
    def test_create_memory(self):
        m = Memory(
            content="用户喜欢 Python",
            memory_type=MemoryType.PREFERENCE,
            importance=MemoryImportance.HIGH,
        )
        assert m.content == "用户喜欢 Python"
        assert m.memory_type == MemoryType.PREFERENCE
        assert m.importance == MemoryImportance.HIGH

    def test_memory_types(self):
        assert MemoryType.FACT.value == "fact"
        assert MemoryType.PREFERENCE.value == "preference"
        assert MemoryType.SKILL.value == "skill"


class TestBuiltinMemoryProvider:
    @pytest.mark.asyncio
    async def test_store_and_retrieve(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "memory.db")
            provider = BuiltinMemoryProvider(db_path=db_path)
            await provider.initialize()

            memory = Memory(
                content="用户名叫张三",
                memory_type=MemoryType.FACT,
                importance=MemoryImportance.HIGH,
                user_id="user1",
            )
            mid = await provider.store(memory)
            assert mid

            # 按 ID 检索
            retrieved = await provider.retrieve(mid)
            assert retrieved is not None
            assert "张三" in retrieved.content

            await provider.close()

    @pytest.mark.asyncio
    async def test_delete_memory(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "memory.db")
            provider = BuiltinMemoryProvider(db_path=db_path)
            await provider.initialize()

            memory = Memory(content="temporary", memory_type=MemoryType.CONTEXT)
            mid = await provider.store(memory)

            deleted = await provider.delete(mid)
            assert deleted is True

            retrieved = await provider.retrieve(mid)
            assert retrieved is None

            await provider.close()

    @pytest.mark.asyncio
    async def test_list_memories(self):
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "memory.db")
            provider = BuiltinMemoryProvider(db_path=db_path)
            await provider.initialize()

            await provider.store(Memory(content="fact1", memory_type=MemoryType.FACT))
            await provider.store(Memory(content="pref1", memory_type=MemoryType.PREFERENCE))
            await provider.store(Memory(content="fact2", memory_type=MemoryType.FACT))

            # list_memories supports memory_type filter
            facts = await provider.list_memories(memory_type=MemoryType.FACT)
            assert len(facts) == 2

            prefs = await provider.list_memories(memory_type=MemoryType.PREFERENCE)
            assert len(prefs) == 1

            await provider.close()
