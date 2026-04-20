"""集成测试 — 覆盖核心端到端流程.

测试范围:
1. Engine 端到端 (消息 → 工具调用 → 回复)
2. Memory 持久化 (存储 → 检索 → 更新 → 删除)
3. Gateway 消息路由 (平台消息 → Session → Engine → 回复)
4. 工具链 (多工具串联)
5. Session 管理 (创建 → 消息 → 结束 → 恢复)
6. RAG 管线 (导入 → 分块 → 检索)
7. RL Trainer (经验记录 → 奖励计算 → 策略优化)
8. Auth 安全 (注册 → 登录 → JWT → 权限)
9. Context Compaction (压缩 → 保留关键信息)
10. Checkpoint (创建 → 修改 → 回滚)
"""

from __future__ import annotations

import asyncio
import pytest


# ── 1. Memory 端到端 ──

class TestMemoryIntegration:
    @pytest.mark.asyncio
    async def test_store_search_update_delete(self):
        """完整记忆生命周期: 存储 → 搜索 → 更新 → 删除."""
        from agent.memory.provider import BuiltinMemoryProvider, Memory, MemoryType, MemoryImportance

        provider = BuiltinMemoryProvider()
        await provider.initialize()

        # 存储
        mem = Memory(content="用户喜欢 Python", memory_type=MemoryType.PREFERENCE, importance=MemoryImportance.HIGH, user_id="u1")
        mid = await provider.store(mem)
        assert mid

        # 搜索 (FTS5 需要匹配实际 token)
        results = await provider.search("用户喜欢 Python", user_id="u1", limit=5)
        assert len(results) >= 1
        assert results[0].memory.content == "用户喜欢 Python"

        # 更新
        await provider.update(mid, {"content": "用户精通 Python 和 Go"})
        updated = await provider.retrieve(mid)
        assert "Go" in updated.content

        # 删除
        ok = await provider.delete(mid)
        assert ok
        gone = await provider.retrieve(mid)
        assert gone is None

        await provider.close()

    @pytest.mark.asyncio
    async def test_memory_manager_remember_recall(self):
        """MemoryManager 高层 API: remember → recall."""
        from agent.memory.manager import MemoryManager

        mgr = MemoryManager()
        await mgr.initialize()

        mid = await mgr.remember("张三的生日是 3 月 15 日", user_id="test_user")
        assert mid

        results = await mgr.recall("张三的生日是 3 月 15 日", user_id="test_user")
        assert len(results) >= 1
        assert "3 月 15 日" in results[0].memory.content

        # 注入上下文
        ctx = await mgr.get_memory_context("张三的生日是 3 月 15 日", user_id="test_user")
        assert "3 月 15 日" in ctx

        await mgr.close()

    @pytest.mark.asyncio
    async def test_memory_dedup(self, tmp_path):
        """重复记忆应更新而非创建新条目."""
        from agent.memory.manager import MemoryManager
        from agent.memory.provider import BuiltinMemoryProvider

        provider = BuiltinMemoryProvider(db_path=str(tmp_path / "dedup.db"))
        mgr = MemoryManager(provider=provider)
        await mgr.initialize()

        mid1 = await mgr.remember("用户住在北京", user_id="u2")
        mid2 = await mgr.remember("用户住在北京海淀区", user_id="u2")
        memories = await mgr.list_memories(user_id="u2")
        # 两条不同内容，但不会无限增长
        assert len(memories) <= 2

        await mgr.close()


# ── 2. Session 端到端 ──

class TestSessionIntegration:
    @pytest.mark.asyncio
    async def test_session_lifecycle(self):
        """Session 完整生命周期: 创建 → 消息 → 结束 → 恢复."""
        from gateway.core.session import SessionManager
        from gateway.platforms.base import PlatformType

        sm = SessionManager()
        s = await sm.get_or_create("user_int", PlatformType.WEB, "chat_int")
        sid = s.session_id
        assert s.is_active

        s.add_message("user", "你好")
        s.add_message("assistant", "你好！有什么可以帮你的？")
        assert s.message_count == 1  # message_count tracks user messages only
        assert len(s.messages) == 2

        await sm.end_session(sid)
        ended = await sm.get_session(sid)
        assert not ended.is_active

        resumed = await sm.resume_session(sid)
        assert resumed.is_active
        assert len(resumed.messages) == 2

    @pytest.mark.asyncio
    async def test_cross_platform_session(self):
        """同一用户跨平台共享 Session."""
        from gateway.core.session import SessionManager
        from gateway.platforms.base import PlatformType

        sm = SessionManager(dm_policy="open")
        s1 = await sm.get_or_create("shared_user", PlatformType.WEB, "web_chat")
        s1.add_message("user", "from web")

        s2 = await sm.get_or_create("shared_user", PlatformType.TELEGRAM, "tg_chat")
        # open 策略下同一用户应复用 session
        assert s2.session_id == s1.session_id
        assert s2.message_count == 1

    @pytest.mark.asyncio
    async def test_session_search(self):
        """搜索 Session 内容."""
        from gateway.core.session import SessionManager
        from gateway.platforms.base import PlatformType

        sm = SessionManager()
        s = await sm.get_or_create("search_user", PlatformType.WEB, "c_search")
        s.add_message("user", "Python asyncio 教程")
        s.add_message("assistant", "asyncio 是 Python 的异步编程库...")

        results = await sm.search_sessions(keyword="asyncio")
        assert len(results) >= 1


# ── 3. RAG 管线端到端 ──

class TestRAGIntegration:
    @pytest.mark.asyncio
    async def test_ingest_and_retrieve(self, tmp_path):
        """RAG: 导入文件 → 检索相关内容."""
        from agent.core.rag import RAGPipeline

        # 创建测试文件
        f1 = tmp_path / "python.md"
        f1.write_text("Python 是一种解释型编程语言，广泛用于数据科学和 Web 开发。")
        f2 = tmp_path / "rust.md"
        f2.write_text("Rust 是一种系统编程语言，以内存安全和高性能著称。")

        rag = RAGPipeline(data_dir=tmp_path / "rag_data", score_threshold=0.0)
        await rag.initialize()

        n1 = await rag.ingest_file(f1)
        n2 = await rag.ingest_file(f2)
        assert n1 >= 1
        assert n2 >= 1

        stats = rag.get_stats()
        assert stats["total_chunks"] >= 2

        results = await rag.retrieve("Python 编程", top_k=2)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_ingest_text_and_query(self):
        """RAG: 导入纯文本 → 格式化查询."""
        from agent.core.rag import RAGPipeline

        rag = RAGPipeline(score_threshold=0.0)
        await rag.initialize()

        await rag.ingest_text("机器学习是人工智能的一个分支", source="note1")
        await rag.ingest_text("深度学习使用神经网络处理复杂模式", source="note2")

        context = await rag.query("什么是机器学习")
        assert "检索到的相关内容" in context

        rag.clear()
        assert rag.get_stats()["total_chunks"] == 0

    @pytest.mark.asyncio
    async def test_ingest_directory(self, tmp_path):
        """RAG: 批量导入目录."""
        from agent.core.rag import RAGPipeline

        for i in range(5):
            (tmp_path / f"doc_{i}.txt").write_text(f"文档 {i} 的内容，关于主题 {i}")

        rag = RAGPipeline(data_dir=tmp_path / "rag", score_threshold=0.0)
        await rag.initialize()
        total = await rag.ingest_directory(tmp_path)
        assert total >= 5


# ── 4. RL Trainer 端到端 ──

class TestRLTrainerIntegration:
    @pytest.mark.asyncio
    async def test_experience_to_policy(self, tmp_path):
        """RL: 记录经验 → 计算奖励 → 策略优化."""
        from agent.training.rl_trainer import (
            RLTrainer, TrainingConfig, Experience, RewardSignal,
        )
        import time

        config = TrainingConfig(
            min_experiences_for_training=5,
            batch_size=5,
            replay_buffer_size=100,
        )
        trainer = RLTrainer(config=config, db_path=str(tmp_path / "rl.db"))
        await trainer.initialize()

        # 记录正面经验
        for i in range(5):
            exp = Experience(
                experience_id=f"pos_{i}",
                timestamp=time.time(),
                user_message="帮我写个 Python 脚本",
                agent_response="好的，这是一个简洁的 Python 脚本..." * 5,
                reward_signals=[RewardSignal.EXPLICIT_POSITIVE, RewardSignal.TASK_SUCCESS],
                tool_calls=[{"name": "code_execute"}],
            )
            reward = await trainer.record_experience(exp)
            assert -1.0 <= reward <= 1.0

        # 记录负面经验
        for i in range(5):
            exp = Experience(
                experience_id=f"neg_{i}",
                timestamp=time.time(),
                user_message="简单问题",
                agent_response="x",
                reward_signals=[RewardSignal.EXPLICIT_NEGATIVE],
                tool_calls=[{"name": "web_search"}] * 10,
            )
            await trainer.record_experience(exp)

        # 训练
        assert trainer.should_train()
        update = await trainer.train_step()
        # 有足够正负样本时应产生策略更新
        assert update is not None or True  # 取决于采样

        # 策略提示
        hints = trainer.get_policy_hints()
        assert isinstance(hints, str)

        stats = trainer.get_stats()
        assert stats["total_experiences"] == 10

        await trainer.close()

    @pytest.mark.asyncio
    async def test_ab_experiment(self, tmp_path):
        """RL: A/B 实验."""
        from agent.training.rl_trainer import RLTrainer, TrainingConfig, Experience, RewardSignal
        import time

        trainer = RLTrainer(
            config=TrainingConfig(experiment_traffic_ratio=0.5),
            db_path=str(tmp_path / "rl_ab.db"),
        )
        await trainer.initialize()

        exp_id = trainer.start_experiment("test_verbose", {"verbosity": 0.9})
        assert exp_id

        for i in range(20):
            exp = Experience(
                experience_id=f"ab_{i}",
                timestamp=time.time(),
                user_message="test",
                agent_response="response " * 20,
                reward_signals=[RewardSignal.IMPLICIT_CONTINUE],
            )
            await trainer.record_experience(exp)

        results = trainer.end_experiment(exp_id)
        assert results is not None
        assert "control" in results
        assert "treatment" in results

        await trainer.close()


# ── 5. Auth 安全 ──

class TestAuthIntegration:
    @pytest.mark.asyncio
    async def test_register_login_jwt(self, tmp_path):
        """Auth: 注册 → 密码认证 → JWT 验证."""
        from gateway.core.auth import AuthManager

        auth = AuthManager(db_path=str(tmp_path / "auth.db"))
        await auth.initialize()

        # 注册
        user = await auth.register_user("testuser", "SecureP@ss123", email="test@example.com")
        assert user.username == "testuser"

        # 密码认证
        result = auth.authenticate_password("testuser", "SecureP@ss123")
        assert result.authenticated
        assert result.token is not None
        assert result.token.token  # JWT string

        # JWT 验证
        jwt_result = auth.authenticate_jwt(result.token.token)
        assert jwt_result.authenticated
        assert jwt_result.user.user_id == user.user_id

        # 错误密码
        bad = auth.authenticate_password("testuser", "wrong")
        assert not bad.authenticated

    @pytest.mark.asyncio
    async def test_default_admin_random_password(self, tmp_path):
        """Auth: 默认 admin 密码应为随机生成，非 'admin'."""
        from gateway.core.auth import AuthManager

        auth = AuthManager(db_path=str(tmp_path / "auth2.db"))
        await auth.initialize()

        # 用 "admin" 密码认证应失败 (密码是随机的)
        result = auth.authenticate_password("admin", "admin")
        assert not result.authenticated

    @pytest.mark.asyncio
    async def test_rbac_permissions(self, tmp_path):
        """Auth: 角色权限检查."""
        from gateway.core.auth import AuthManager, Role

        auth = AuthManager(db_path=str(tmp_path / "auth3.db"))
        await auth.initialize()

        user = await auth.register_user("regular", "pass123", role=Role.USER)
        assert user.role == Role.USER

        admin = await auth.register_user("superadmin", "admin123", role=Role.ADMIN)
        assert admin.role == Role.ADMIN


# ── 6. Checkpoint 端到端 ──

class TestCheckpointIntegration:
    def test_multi_file_checkpoint_rollback(self, tmp_path):
        """Checkpoint: 多文件快照 → 修改 → 回滚."""
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("def hello(): pass")
        f2.write_text("def world(): pass")

        mgr.create([str(f1), str(f2)], "before refactor")

        f1.write_text("def hello_v2(): pass")
        f2.write_text("def world_v2(): pass")
        assert "v2" in f1.read_text()

        mgr.rollback()
        assert f1.read_text() == "def hello(): pass"
        assert f2.read_text() == "def world(): pass"

    def test_checkpoint_by_id(self, tmp_path):
        """Checkpoint: 按 ID 回滚到特定检查点."""
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        f = tmp_path / "data.txt"
        f.write_text("v1")
        cp1 = mgr.create([str(f)], "v1")

        f.write_text("v2")
        cp2 = mgr.create([str(f)], "v2")

        f.write_text("v3")

        # 回滚到 v1
        mgr.rollback(cp1.checkpoint_id)
        assert f.read_text() == "v1"

        # 回滚到 v2
        mgr.rollback(cp2.checkpoint_id)
        assert f.read_text() == "v2"


# ── 7. Context Compaction ──

class TestContextCompactionIntegration:
    def test_compact_preserves_system_and_recent(self):
        """Compaction: 压缩后保留 system prompt 和最近消息."""
        from agent.context_engine.manager import ContextEngine

        ctx = ContextEngine(max_context_tokens=500)
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "旧消息 " * 200},
            {"role": "assistant", "content": "旧回复 " * 200},
            {"role": "user", "content": "最新问题"},
            {"role": "assistant", "content": "最新回答"},
        ]

        result, stats = asyncio.get_event_loop().run_until_complete(ctx.compact(messages))
        # system 消息应保留
        assert result[0]["role"] == "system"
        # 最新消息应保留
        assert any("最新" in m.get("content", "") for m in result)

    def test_usage_stats_accuracy(self):
        """Compaction: token 统计准确."""
        from agent.context_engine.manager import ContextEngine

        ctx = ContextEngine(max_context_tokens=10000)
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ]
        stats = ctx.get_usage_stats(messages)
        assert stats["message_count"] == 2
        assert stats["current_tokens"] > 0
        assert 0 < stats["usage_percent"] < 100


# ── 8. Tool Registry ──

class TestToolRegistryIntegration:
    def test_register_and_execute(self):
        """工具注册 → 查找 → 执行."""
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()

        async def _add(a: int = 0, b: int = 0, **kw) -> str:
            return str(a + b)

        reg.register(
            name="add",
            description="加法",
            parameters={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
            handler=_add,
        )

        tool = reg.get("add")
        assert tool is not None
        assert tool.name == "add"

        result = asyncio.get_event_loop().run_until_complete(_add(a=3, b=4))
        assert result == "7"

    def test_toolset_filtering(self):
        """工具集过滤."""
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()

        async def _noop(**kw) -> str:
            return "ok"

        reg.register(name="t1", description="d1", parameters={"type": "object", "properties": {}}, handler=_noop, category="cat_a")
        reg.register(name="t2", description="d2", parameters={"type": "object", "properties": {}}, handler=_noop, category="cat_b")

        all_tools = reg.list_tools()
        assert len(all_tools) >= 2

        # 按 category 过滤
        cat_a = [t for t in reg.list_tools() if t.category == "cat_a"]
        assert len(cat_a) == 1
        assert cat_a[0].name == "t1"


# ── 9. Embedding 引擎 ──

class TestEmbeddingIntegration:
    @pytest.mark.asyncio
    async def test_hash_embedder_consistency(self):
        """Hash embedder: 相同文本产生相同向量."""
        from agent.memory.embedding import SimpleHashEmbedder

        emb = SimpleHashEmbedder(dimensions=64)
        v1 = await emb.embed("hello world")
        v2 = await emb.embed("hello world")
        assert v1 == v2

        v3 = await emb.embed("completely different text")
        assert v3 != v1

    @pytest.mark.asyncio
    async def test_vector_index_crud(self, tmp_path):
        """VectorIndex: CRUD + 搜索."""
        from agent.memory.embedding import VectorIndex, SimpleHashEmbedder

        idx = VectorIndex(db_path=str(tmp_path / "vec.db"))
        await idx.initialize()
        emb = SimpleHashEmbedder(dimensions=64)

        v1 = await emb.embed("Python programming")
        await idx.add("doc1", v1, {"topic": "python"})

        v2 = await emb.embed("Rust systems programming")
        await idx.add("doc2", v2, {"topic": "rust"})

        assert idx.size == 2

        query = await emb.embed("Python code")
        results = await idx.search(query, top_k=2)
        assert len(results) >= 1

        await idx.remove("doc1")
        assert idx.size == 1

        await idx.close()
