"""测试 — Context Engine 上下文管理."""

from __future__ import annotations

import pytest

from agent.context_engine.manager import ContextEngine
from agent.providers.base import Message


class TestContextEngine:
    """测试上下文引擎."""

    def test_estimate_tokens(self):
        engine = ContextEngine()
        tokens = engine.estimate_tokens("Hello, world!")
        assert tokens > 0
        # 13 chars * 0.4 ≈ 5 tokens
        assert 3 <= tokens <= 10

    def test_estimate_tokens_empty(self):
        engine = ContextEngine()
        assert engine.estimate_tokens("") == 0

    def test_estimate_tokens_chinese(self):
        engine = ContextEngine()
        tokens = engine.estimate_tokens("你好世界")
        assert tokens > 0

    def test_count_message_tokens(self):
        engine = ContextEngine()
        messages = [
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content="Hello!"),
        ]
        total = engine.count_message_tokens(messages)
        assert total > 0

    def test_effective_max(self):
        engine = ContextEngine(
            max_context_tokens=100000,
            reserve_for_response=4096,
        )
        assert engine.effective_max == 95904

    @pytest.mark.asyncio
    async def test_manage_under_limit(self):
        engine = ContextEngine(max_context_tokens=100000)
        messages = [
            Message(role="system", content="System prompt"),
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi!"),
        ]

        result = await engine.manage(messages)
        assert len(result) == 3  # 没有变化

    @pytest.mark.asyncio
    async def test_manage_sliding_window(self):
        engine = ContextEngine(
            max_context_tokens=100,
            reserve_for_response=10,
            min_recent_turns=1,
        )

        # 创建大量消息超过限制
        messages = [
            Message(role="system", content="System"),
        ]
        for i in range(20):
            messages.append(Message(role="user", content=f"Message {i} " * 20))
            messages.append(Message(role="assistant", content=f"Reply {i} " * 20))

        result = await engine.manage(messages)
        # 应该裁剪了
        assert len(result) < len(messages)

    @pytest.mark.asyncio
    async def test_manage_preserves_system(self):
        engine = ContextEngine(
            max_context_tokens=50,
            reserve_for_response=5,
            min_recent_turns=1,
        )

        messages = [
            Message(role="system", content="Important system prompt"),
            Message(role="user", content="Long message " * 100),
            Message(role="assistant", content="Long reply " * 100),
            Message(role="user", content="Latest question"),
            Message(role="assistant", content="Latest answer"),
        ]

        result = await engine.manage(messages)
        # System prompt 应该保留
        assert result[0].role == "system"
        assert "Important" in result[0].content
