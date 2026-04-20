"""测试 — 竞品对齐第四轮: Checkpoints, Context Compaction, Session Resume, OpenAI API, CDP, CLI flags."""

from __future__ import annotations

import pytest


# ── Checkpoints & Rollback ──

class TestCheckpointManager:
    def test_create_checkpoint(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        # 创建测试文件
        f = tmp_path / "test.txt"
        f.write_text("original content")

        cp = mgr.create([str(f)], "before edit")
        assert cp.checkpoint_id
        assert cp.file_count == 1
        assert cp.description == "before edit"

    def test_rollback(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        f = tmp_path / "test.txt"
        f.write_text("original")

        mgr.create([str(f)], "snapshot")

        # 修改文件
        f.write_text("modified")
        assert f.read_text() == "modified"

        # 回滚
        cp = mgr.rollback()
        assert cp is not None
        assert f.read_text() == "original"

    def test_rollback_deleted_file(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        f = tmp_path / "test.txt"
        f.write_text("content")
        mgr.create([str(f)])

        f.unlink()
        assert not f.exists()

        mgr.rollback()
        assert f.exists()
        assert f.read_text() == "content"

    def test_list_checkpoints(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        f = tmp_path / "a.txt"
        f.write_text("a")
        mgr.create([str(f)], "cp1")
        mgr.create([str(f)], "cp2")

        cps = mgr.list_checkpoints()
        assert len(cps) == 2

    def test_prune(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"), max_checkpoints=2)
        mgr.initialize()

        f = tmp_path / "a.txt"
        f.write_text("a")
        mgr.create([str(f)], "cp1")
        mgr.create([str(f)], "cp2")
        mgr.create([str(f)], "cp3")

        assert len(mgr.list_checkpoints()) == 2

    def test_clear(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()

        f = tmp_path / "a.txt"
        f.write_text("a")
        mgr.create([str(f)])
        count = mgr.clear()
        assert count == 1
        assert len(mgr.list_checkpoints()) == 0

    def test_disabled(self, tmp_path):
        from agent.core.checkpoint import CheckpointManager

        mgr = CheckpointManager(checkpoint_dir=str(tmp_path / "cp"))
        mgr.initialize()
        mgr.enabled = False

        f = tmp_path / "a.txt"
        f.write_text("a")
        cp = mgr.create([str(f)])
        assert cp.file_count == 0  # disabled, no files snapshotted


# ── Context Compaction ──

class TestContextCompaction:
    def test_compact_basic(self):
        from agent.context_engine.manager import ContextEngine

        ctx = ContextEngine(max_context_tokens=1000)
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello " * 100},
            {"role": "assistant", "content": "Hi " * 100},
            {"role": "user", "content": "What?"},
            {"role": "assistant", "content": "Sure."},
        ]

        import asyncio
        result, stats = asyncio.get_event_loop().run_until_complete(ctx.compact(messages))
        assert stats["before_messages"] == 5
        assert stats["after_messages"] <= 5
        assert stats["saved_tokens"] >= 0

    def test_should_auto_compact(self):
        from agent.context_engine.manager import ContextEngine

        ctx = ContextEngine(max_context_tokens=100, summary_threshold=0.5)
        # 大量消息 — 超过阈值
        messages = [{"role": "user", "content": "x" * 500}]
        assert ctx.should_auto_compact(messages) is True

        # 少量消息 — 不超过阈值
        ctx2 = ContextEngine(max_context_tokens=100000, summary_threshold=0.8)
        small = [{"role": "user", "content": "hi"}]
        assert ctx2.should_auto_compact(small) is False

    def test_usage_stats(self):
        from agent.context_engine.manager import ContextEngine

        ctx = ContextEngine(max_context_tokens=10000)
        messages = [{"role": "user", "content": "hello world"}]
        stats = ctx.get_usage_stats(messages)
        assert "current_tokens" in stats
        assert "usage_percent" in stats
        assert stats["message_count"] == 1


# ── Session Resume ──

class TestSessionResume:
    @pytest.mark.asyncio
    async def test_resume_session(self):
        from gateway.core.session import SessionManager, Session
        from gateway.platforms.base import PlatformType

        sm = SessionManager()
        session = await sm.get_or_create("user1", PlatformType.WEB, "chat1")
        session.add_message("user", "hello")
        sid = session.session_id

        # 结束会话
        await sm.end_session(sid)
        ended = await sm.get_session(sid)
        assert ended.is_active is False

        # 恢复
        resumed = await sm.resume_session(sid)
        assert resumed is not None
        assert resumed.is_active is True
        assert resumed.message_count == 1

    @pytest.mark.asyncio
    async def test_search_sessions(self):
        from gateway.core.session import SessionManager
        from gateway.platforms.base import PlatformType

        sm = SessionManager()
        s1 = await sm.get_or_create("user1", PlatformType.WEB, "c1")
        s1.add_message("user", "hello world")
        s2 = await sm.get_or_create("user2", PlatformType.TELEGRAM, "c2")
        s2.add_message("user", "goodbye")

        results = await sm.search_sessions(keyword="hello")
        assert len(results) >= 1
        assert any("hello" in r.get("preview", "") for r in results)


# ── OpenAI-compatible API ──

class TestOpenAIAPI:
    def test_api_config(self):
        from web.openai_api import APIConfig

        cfg = APIConfig(host="localhost", port=9090, api_key="sk-test")
        assert cfg.host == "localhost"
        assert cfg.port == 9090
        assert cfg.api_key == "sk-test"

    def test_server_creation(self):
        from web.openai_api import OpenAIAPIServer, APIConfig

        server = OpenAIAPIServer(config=APIConfig(model_name="test-model"))
        assert server._config.model_name == "test-model"
        assert server._request_count == 0


# ── Chrome CDP ──

class TestChromeCDP:
    def test_get_page_accepts_cdp_url(self):
        """验证 _get_page 函数签名接受 cdp_url 参数."""
        import inspect
        from agent.tools.browser import _get_page

        sig = inspect.signature(_get_page)
        assert "cdp_url" in sig.parameters

    def test_browser_action_accepts_cdp_url(self):
        """验证 _browser_action 函数签名接受 cdp_url 参数."""
        import inspect
        from agent.tools.browser import _browser_action

        sig = inspect.signature(_browser_action)
        assert "cdp_url" in sig.parameters


# ── CLI Flags ──

class TestCLIFlags:
    def test_chat_command_has_yolo_flag(self):
        from cli.main import chat
        param_names = [p.name for p in chat.params]
        assert "yolo" in param_names

    def test_chat_command_has_worktree_flag(self):
        from cli.main import chat
        param_names = [p.name for p in chat.params]
        assert "worktree" in param_names

    def test_chat_command_has_session_flag(self):
        from cli.main import chat
        param_names = [p.name for p in chat.params]
        assert "resume_session" in param_names

    def test_serve_api_command_exists(self):
        from cli.main import cli
        commands = list(cli.commands.keys())
        assert "serve-api" in commands

    def test_serve_api_has_api_key_option(self):
        from cli.main import serve_api
        param_names = [p.name for p in serve_api.params]
        assert "api_key" in param_names
