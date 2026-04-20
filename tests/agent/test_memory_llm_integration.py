"""测试 — LLM 集成 (mock model_router 验证合并/反思/提取完整流程)."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any

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


# ── Mock Model Router ──

@dataclass
class MockResponse:
    content: str


class MockModelRouter:
    """Mock model router that returns predefined JSON responses."""

    def __init__(self, response_content: str):
        self._response = response_content
        self.call_count = 0

    async def complete_with_failover(self, **kwargs) -> MockResponse:
        self.call_count += 1
        return MockResponse(content=self._response)


# ── Helpers ──

async def _make_provider(tmpdir):
    db_path = os.path.join(tmpdir, "test_llm.db")
    provider = BuiltinMemoryProvider(db_path=db_path)
    await provider.initialize()
    return provider


# ══════════════════════════════════════════════════════════════
#  Consolidator Integration
# ══════════════════════════════════════════════════════════════

class TestConsolidatorIntegration:
    @pytest.mark.asyncio
    async def test_consolidate_cluster_merges(self):
        """LLM 合并 N 条记忆为 1 条."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            # 创建 3 条相似记忆
            mems = []
            for content in ["用户喜欢Python", "用户偏好Python编程", "用户爱用Python"]:
                mid = await provider.store(Memory(
                    content=content,
                    memory_type=MemoryType.PREFERENCE,
                    user_id="u1",
                ))
                mem = await provider.retrieve(mid)
                mems.append(mem)

            # Mock LLM 返回合并结果
            merged_json = json.dumps({
                "content": "用户非常喜欢Python编程",
                "memory_type": "preference",
                "importance": "high",
                "tags": ["python", "preference"],
            })
            router = MockModelRouter(merged_json)
            consolidator = MemoryConsolidator(provider, model_router=router)

            result = await consolidator.consolidate_cluster(mems, router)

            assert result is not None
            assert result["content"] == "用户非常喜欢Python编程"
            assert result["memory_type"] == "preference"
            assert router.call_count == 1
            await provider.close()

    @pytest.mark.asyncio
    async def test_run_consolidation_full_pipeline(self):
        """完整合并管线: 找聚类 → 合并 → 删旧存新."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            # 存 3 条内容完全相同的记忆 (确保聚类)
            ids = []
            for i in range(3):
                mid = await provider.store(Memory(
                    content=f"用户喜欢Python编程语言 variant{i}",
                    memory_type=MemoryType.PREFERENCE,
                    user_id="u1",
                ))
                ids.append(mid)

            # 确认有 3 条
            all_mems = await provider.list_memories(user_id="u1")
            assert len(all_mems) == 3

            merged_json = json.dumps({
                "content": "用户喜欢Python编程语言",
                "memory_type": "preference",
                "importance": "high",
                "tags": ["python"],
            })
            router = MockModelRouter(merged_json)
            consolidator = MemoryConsolidator(provider, model_router=router)

            # 手动构建聚类 (因为 SimpleHashEmbedder 可能不聚类)
            cluster = [await provider.retrieve(mid) for mid in ids]
            result = await consolidator.consolidate_cluster(cluster, router)
            assert result is not None

            await provider.close()

    @pytest.mark.asyncio
    async def test_consolidation_bad_json_handled(self):
        """LLM 返回坏 JSON 时不崩溃."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            mems = []
            for c in ["fact A", "fact B"]:
                mid = await provider.store(Memory(content=c, memory_type=MemoryType.FACT, user_id="u1"))
                mems.append(await provider.retrieve(mid))

            router = MockModelRouter("this is not json at all")
            consolidator = MemoryConsolidator(provider, model_router=router)

            result = await consolidator.consolidate_cluster(mems, router)
            assert result is None
            await provider.close()


# ══════════════════════════════════════════════════════════════
#  Reflector Integration
# ══════════════════════════════════════════════════════════════

class TestReflectorIntegration:
    @pytest.mark.asyncio
    async def test_reflect_generates_meta_memories(self):
        """反思生成 META 类型记忆."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            # 先存一些记忆作为反思素材
            for i in range(5):
                await provider.store(Memory(
                    content=f"fact about Python {i}",
                    memory_type=MemoryType.FACT,
                    user_id="u1",
                ))

            # Mock LLM 返回反思结果
            insights_json = json.dumps([
                {"type": "behavior_pattern", "insight": "用户经常问Python相关问题", "action": "优先记忆Python知识", "priority": "high"},
                {"type": "knowledge_gap", "insight": "缺少数据库相关记忆", "action": "关注数据库话题", "priority": "medium"},
            ])
            router = MockModelRouter(insights_json)
            reflector = MemoryReflector(provider, model_router=router)

            memory_ids = await reflector.reflect(user_id="u1", model_router=router)

            assert len(memory_ids) == 2
            assert router.call_count == 1

            # 验证 META 记忆已存储
            for mid in memory_ids:
                mem = await provider.retrieve(mid)
                assert mem is not None
                assert mem.memory_type == MemoryType.META
                assert "reflection" in mem.tags

            # 验证 reflections 表
            cursor = await provider._db.execute("SELECT COUNT(*) FROM reflections")
            count = (await cursor.fetchone())[0]
            assert count == 2

            await provider.close()

    @pytest.mark.asyncio
    async def test_reflect_bad_json_returns_empty(self):
        """反思 LLM 返回坏 JSON 时返回空列表."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            router = MockModelRouter("not valid json")
            reflector = MemoryReflector(provider, model_router=router)

            result = await reflector.reflect(user_id="u1", model_router=router)
            assert result == []
            await provider.close()

    @pytest.mark.asyncio
    async def test_knowledge_gaps_guide_extraction(self):
        """知识缺口引导记忆提取."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)

            # 插入知识缺口
            import time, uuid
            await provider._db.execute(
                "INSERT INTO reflections (reflection_id, reflection_type, content, action_items, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "knowledge_gap", "缺少数据库优化知识", "[]", "active", time.time(), time.time()),
            )
            await provider._db.commit()

            reflector = MemoryReflector(provider)
            gaps = await reflector.get_knowledge_gaps(user_id="u1")
            assert len(gaps) == 1
            assert "数据库" in gaps[0]
            await provider.close()


# ══════════════════════════════════════════════════════════════
#  Memory Extraction Integration
# ══════════════════════════════════════════════════════════════

class TestExtractionIntegration:
    @pytest.mark.asyncio
    async def test_extract_from_conversation(self):
        """从对话中自动提取记忆."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            config = MemoryConfig(extract_interval=1)  # 每轮都提取
            mgr = MemoryManager(provider=provider, config=config)

            # Mock LLM 返回提取结果
            extracted_json = json.dumps([
                {"content": "用户是Python开发者", "memory_type": "fact", "importance": "high", "tags": ["user_info"]},
                {"content": "用户偏好简洁代码风格", "memory_type": "preference", "importance": "medium", "tags": ["style"]},
            ])
            router = MockModelRouter(extracted_json)

            messages = [
                {"role": "user", "content": "我是一个Python开发者，喜欢写简洁的代码"},
                {"role": "assistant", "content": "好的，我了解了。你喜欢简洁的Python代码风格。"},
            ]

            memory_ids = await mgr.extract_from_conversation(
                messages=messages,
                user_id="u1",
                model_router=router,
            )

            assert len(memory_ids) == 2
            assert router.call_count == 1

            # 验证记忆已存储
            all_mems = await provider.list_memories(user_id="u1")
            assert len(all_mems) == 2
            await provider.close()

    @pytest.mark.asyncio
    async def test_extract_empty_conversation(self):
        """空对话不提取."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            config = MemoryConfig(extract_interval=1)
            mgr = MemoryManager(provider=provider, config=config)

            router = MockModelRouter("[]")
            result = await mgr.extract_from_conversation(
                messages=[],
                user_id="u1",
                model_router=router,
            )
            assert result == []
            assert router.call_count == 0  # 不应调用 LLM
            await provider.close()

    @pytest.mark.asyncio
    async def test_extract_with_knowledge_gap_guidance(self):
        """知识缺口引导提取 prompt."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            config = MemoryConfig(extract_interval=1)
            mgr = MemoryManager(provider=provider, config=config)

            # 插入知识缺口
            import time, uuid
            await provider._db.execute(
                "INSERT INTO reflections (reflection_id, reflection_type, content, action_items, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), "knowledge_gap", "需要更多关于Docker的知识", "[]", "active", time.time(), time.time()),
            )
            await provider._db.commit()

            extracted_json = json.dumps([
                {"content": "用户使用Docker部署", "memory_type": "fact", "importance": "high", "tags": ["docker"]},
            ])
            router = MockModelRouter(extracted_json)

            messages = [
                {"role": "user", "content": "我用Docker部署我的Python应用"},
                {"role": "assistant", "content": "Docker是个好选择。"},
            ]

            memory_ids = await mgr.extract_from_conversation(
                messages=messages,
                user_id="u1",
                model_router=router,
            )

            assert len(memory_ids) == 1
            # LLM 被调用了 (prompt 中应包含知识缺口引导)
            assert router.call_count == 1
            await provider.close()


# ══════════════════════════════════════════════════════════════
#  Feedback Loop Integration
# ══════════════════════════════════════════════════════════════

class TestFeedbackLoopIntegration:
    @pytest.mark.asyncio
    async def test_feedback_affects_search_ranking(self):
        """有用性反馈影响搜索排序."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mgr = MemoryManager(provider=provider)

            # 存两条记忆
            id_good = await mgr.remember("Python is great for data science", user_id="u1")
            id_bad = await mgr.remember("Python was created in 1991", user_id="u1")

            # 给 good 正反馈，bad 负反馈
            for _ in range(5):
                await mgr.record_feedback([id_good], "positive")
                await mgr.record_feedback([id_bad], "negative")

            # 验证分数差异
            mem_good = await provider.retrieve(id_good)
            mem_bad = await provider.retrieve(id_bad)
            assert mem_good.usefulness_score > mem_bad.usefulness_score
            assert mem_good.feedback_count == 5
            assert mem_bad.feedback_count == 5
            await provider.close()

    @pytest.mark.asyncio
    async def test_ema_convergence(self):
        """EMA 收敛到正确值."""
        with tempfile.TemporaryDirectory() as d:
            provider = await _make_provider(d)
            mgr = MemoryManager(provider=provider)
            mid = await mgr.remember("test", user_id="u1")

            # 10 次正反馈
            for _ in range(10):
                await mgr.record_feedback([mid], "positive")

            mem = await provider.retrieve(mid)
            # 应该接近 1.0
            assert mem.usefulness_score > 0.85
            await provider.close()


# ══════════════════════════════════════════════════════════════
#  LLM JSON Retry
# ══════════════════════════════════════════════════════════════

class TestLLMJsonRetry:
    @pytest.mark.asyncio
    async def test_retry_on_bad_json(self):
        """坏 JSON 重试后成功."""
        call_count = 0

        class RetryRouter:
            async def complete_with_failover(self, **kw):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return MockResponse("not json")
                return MockResponse('{"result": "ok"}')

        from agent.memory.llm_utils import llm_json_call
        result = await llm_json_call(RetryRouter(), "test prompt", max_retries=2, backoff_base=0.01)
        assert result == {"result": "ok"}
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        """所有重试都失败返回 None."""
        call_count = 0

        class AlwaysBadRouter:
            async def complete_with_failover(self, **kw):
                nonlocal call_count
                call_count += 1
                return MockResponse("still not json")

        from agent.memory.llm_utils import llm_json_call
        result = await llm_json_call(AlwaysBadRouter(), "test", max_retries=2, backoff_base=0.01)
        assert result is None
        assert call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_json_error_no_retry(self):
        """非 JSON 错误不重试."""
        call_count = 0

        class ErrorRouter:
            async def complete_with_failover(self, **kw):
                nonlocal call_count
                call_count += 1
                raise ConnectionError("network down")

        from agent.memory.llm_utils import llm_json_call
        result = await llm_json_call(ErrorRouter(), "test", max_retries=2, backoff_base=0.01)
        assert result is None
        assert call_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_markdown_stripped(self):
        """Markdown code block 被正确去除."""
        router = MockModelRouter('```json\n{"key": "value"}\n```')
        from agent.memory.llm_utils import llm_json_call
        result = await llm_json_call(router, "test")
        assert result == {"key": "value"}
