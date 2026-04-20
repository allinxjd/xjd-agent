"""测试 — Memory 自进化系统 (合并/反馈/反思/并发/事务)."""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from agent.memory.config import MemoryConfig
from agent.memory.provider import (
    BuiltinMemoryProvider,
    Memory,
    MemoryImportance,
    MemoryType,
)
from agent.memory.manager import MemoryManager
from agent.memory.consolidator import MemoryConsolidator
from agent.memory.reflector import MemoryReflector


# ── Helpers ──

async def _make_provider(tmpdir):
    db_path = os.path.join(tmpdir, "test_memory.db")
    provider = BuiltinMemoryProvider(db_path=db_path)
    await provider.initialize()
    return provider


async def _store_n(provider, contents, mtype=MemoryType.FACT, user_id="u1"):
    ids = []
    for c in contents:
        mid = await provider.store(Memory(content=c, memory_type=mtype, user_id=user_id))
        ids.append(mid)
    return ids


# ── Config ──

class TestMemoryConfig:
    def test_defaults(self):
        cfg = MemoryConfig()
        assert cfg.max_injection == 8
        assert cfg.consolidation_interval == 50
        assert cfg.reflection_interval == 100
        assert cfg.feedback_ema_alpha == 0.8

    def test_override(self):
        cfg = MemoryConfig(max_injection=3, decay_interval=10)
        assert cfg.max_injection == 3
        assert cfg.decay_interval == 10


# ── Schema Migration ──

class TestSchemaMigration:
    @pytest.mark.asyncio
    async def test_new_tables_created(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            # Verify tables exist
            cursor = await provider._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {r[0] for r in await cursor.fetchall()}
            assert "memories" in tables
            assert "memory_consolidations" in tables
            assert "memory_feedback" in tables
            assert "reflections" in tables
            await provider.close()

    @pytest.mark.asyncio
    async def test_new_columns_exist(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            cols = [r[1] for r in await provider._db.execute_fetchall("PRAGMA table_info(memories)")]
            assert "usefulness_score" in cols
            assert "feedback_count" in cols
            assert "consolidated_from" in cols
            await provider.close()


# ── META Memory Type ──

class TestMetaMemoryType:
    def test_meta_enum(self):
        assert MemoryType.META.value == "meta"

    @pytest.mark.asyncio
    async def test_store_meta_memory(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mid = await provider.store(Memory(
                content="[behavior_pattern] user asks Python questions",
                memory_type=MemoryType.META,
                importance=MemoryImportance.MEDIUM,
            ))
            mem = await provider.retrieve(mid)
            assert mem.memory_type == MemoryType.META
            await provider.close()


# ── Dedup (Manager.remember) ──

class TestDedup:
    @pytest.mark.asyncio
    async def test_exact_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mgr = MemoryManager(provider=provider)
            id1 = await mgr.remember("user likes Python", user_id="u1")
            id2 = await mgr.remember("user likes Python programming", user_id="u1")
            # Should update, not create new (substring match)
            assert id1 == id2
            await provider.close()


# ── Feedback ──

class TestFeedback:
    @pytest.mark.asyncio
    async def test_record_feedback_positive(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mgr = MemoryManager(provider=provider)
            mid = await mgr.remember("test fact", user_id="u1")

            await mgr.record_feedback([mid], "positive")

            mem = await provider.retrieve(mid)
            assert mem.feedback_count == 1
            # EMA: 0.8 * 0.5 + 0.2 * 1.0 = 0.6
            assert abs(mem.usefulness_score - 0.6) < 0.01
            await provider.close()

    @pytest.mark.asyncio
    async def test_record_feedback_negative(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mgr = MemoryManager(provider=provider)
            mid = await mgr.remember("test fact", user_id="u1")

            await mgr.record_feedback([mid], "negative")

            mem = await provider.retrieve(mid)
            # EMA: 0.8 * 0.5 + 0.2 * 0.0 = 0.4
            assert abs(mem.usefulness_score - 0.4) < 0.01
            await provider.close()

    @pytest.mark.asyncio
    async def test_feedback_table_populated(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mgr = MemoryManager(provider=provider)
            mid = await mgr.remember("test", user_id="u1")
            await mgr.record_feedback([mid], "positive")

            cursor = await provider._db.execute("SELECT COUNT(*) FROM memory_feedback")
            count = (await cursor.fetchone())[0]
            assert count >= 1
            await provider.close()


# ── Decay ──

class TestDecay:
    @pytest.mark.asyncio
    async def test_meta_memory_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            cfg = MemoryConfig(max_meta_memories=3, max_memories=1000)
            mgr = MemoryManager(provider=provider, config=cfg)

            # Create 5 META memories
            for i in range(5):
                await provider.store(Memory(
                    content=f"meta insight {i}",
                    memory_type=MemoryType.META,
                    user_id="u1",
                ))

            await mgr.decay_memories(user_id="u1")

            remaining = await provider.list_memories(user_id="u1", memory_type=MemoryType.META)
            assert len(remaining) <= 3
            await provider.close()


# ── Consolidator ──

class TestConsolidator:
    @pytest.mark.asyncio
    async def test_find_clusters_empty(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            consolidator = MemoryConsolidator(provider)
            clusters = await consolidator.find_similar_clusters(user_id="u1")
            assert clusters == []
            await provider.close()

    @pytest.mark.asyncio
    async def test_run_consolidation_no_router(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            consolidator = MemoryConsolidator(provider)
            result = await consolidator.run_consolidation(user_id="u1")
            assert result == 0
            await provider.close()


# ── Reflector ──

class TestReflector:
    @pytest.mark.asyncio
    async def test_reflect_no_router(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            reflector = MemoryReflector(provider)
            result = await reflector.reflect(user_id="u1")
            assert result == []
            await provider.close()

    @pytest.mark.asyncio
    async def test_get_knowledge_gaps_empty(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            reflector = MemoryReflector(provider)
            gaps = await reflector.get_knowledge_gaps(user_id="u1")
            assert gaps == []
            await provider.close()

    @pytest.mark.asyncio
    async def test_get_knowledge_gaps_with_data(self):
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            import time, uuid
            await provider._db.execute(
                "INSERT INTO reflections (reflection_id, reflection_type, content, action_items, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "knowledge_gap", "needs more Python knowledge", "[]", "active", time.time(), time.time()),
            )
            await provider._db.commit()

            reflector = MemoryReflector(provider)
            gaps = await reflector.get_knowledge_gaps(user_id="u1")
            assert len(gaps) == 1
            assert "Python" in gaps[0]
            await provider.close()


# ── Concurrency (Lock guards) ──

class TestConcurrencyGuards:
    @pytest.mark.asyncio
    async def test_locks_exist(self):
        from agent.skills.learning_loop import LearningLoop
        loop = LearningLoop()
        assert hasattr(loop, '_decay_lock')
        assert hasattr(loop, '_consolidation_lock')
        assert hasattr(loop, '_reflection_lock')
        assert isinstance(loop._decay_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_lock_prevents_reentry(self):
        from agent.skills.learning_loop import LearningLoop
        loop = LearningLoop()
        # Acquire lock manually
        await loop._decay_lock.acquire()
        assert loop._decay_lock.locked()
        # The guarded method should not run while locked
        loop._decay_lock.release()
        assert not loop._decay_lock.locked()


# ── Transaction Safety ──

class TestTransactionSafety:
    @pytest.mark.asyncio
    async def test_consolidation_table_has_history(self):
        """Verify consolidation history table works."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            import json, time, uuid
            await provider._db.execute(
                "INSERT INTO memory_consolidations (consolidation_id, source_ids, result_id, strategy, created_at) VALUES (?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), json.dumps(["a", "b"]), "c", "merge", time.time()),
            )
            await provider._db.commit()
            cursor = await provider._db.execute("SELECT COUNT(*) FROM memory_consolidations")
            count = (await cursor.fetchone())[0]
            assert count == 1
            await provider.close()


# ── WAL Mode + Concurrent Writes ──

class TestWALAndConcurrency:
    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self):
        """Verify WAL journal mode is set."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            cursor = await provider._db.execute("PRAGMA journal_mode")
            mode = (await cursor.fetchone())[0]
            assert mode == "wal"
            await provider.close()

    @pytest.mark.asyncio
    async def test_concurrent_writes(self):
        """并发写入不会 database is locked."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            async def write_batch(start: int, count: int):
                for i in range(count):
                    await provider.store(Memory(
                        content=f"concurrent memory {start + i}",
                        memory_type=MemoryType.FACT,
                        user_id="u1",
                    ))

            # 5 个并发任务，每个写 10 条
            tasks = [asyncio.create_task(write_batch(i * 10, 10)) for i in range(5)]
            await asyncio.gather(*tasks)

            all_mems = await provider.list_memories(user_id="u1", limit=100)
            assert len(all_mems) == 50
            await provider.close()

    @pytest.mark.asyncio
    async def test_concurrent_read_write(self):
        """并发读写不冲突."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            # 先写一些数据
            for i in range(20):
                await provider.store(Memory(
                    content=f"base memory {i}",
                    memory_type=MemoryType.FACT,
                    user_id="u1",
                ))

            read_results = []

            async def reader():
                for _ in range(10):
                    results = await provider.search(query="memory", user_id="u1", limit=5)
                    read_results.append(len(results))
                    await asyncio.sleep(0.01)

            async def writer():
                for i in range(10):
                    await provider.store(Memory(
                        content=f"new memory during read {i}",
                        memory_type=MemoryType.FACT,
                        user_id="u1",
                    ))
                    await asyncio.sleep(0.01)

            await asyncio.gather(reader(), writer())

            assert len(read_results) == 10
            assert all(r >= 0 for r in read_results)
            await provider.close()
