"""测试 — AgentEngine 核心引擎."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.core.engine import AgentEngine, TurnResult
from agent.core.model_router import ModelRouter
from agent.providers.base import (
    CompletionResponse,
    Message,
    Usage,
)


class TestAgentEngine:
    """测试 AgentEngine."""

    def _make_engine(self) -> tuple[AgentEngine, AsyncMock]:
        mock_router = AsyncMock(spec=ModelRouter)
        mock_router.complete_with_failover = AsyncMock(return_value=CompletionResponse(
            content="Hello!",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="test-model",
            finish_reason="stop",
        ))

        engine = AgentEngine(router=mock_router)
        return engine, mock_router

    def test_init(self):
        engine, _ = self._make_engine()
        assert engine._turn_count == 0
        assert len(engine._messages) == 0
        assert len(engine._tools) == 0

    def test_register_tool(self):
        engine, _ = self._make_engine()

        async def handler(x: str) -> str:
            return x

        engine.register_tool(
            name="test",
            description="test tool",
            parameters={"type": "object", "properties": {}},
            handler=handler,
        )

        assert "test" in engine._tools
        assert len(engine.tool_definitions) == 1

    def test_reset(self):
        engine, _ = self._make_engine()
        engine._messages = [Message(role="user", content="test")]
        engine._turn_count = 5

        engine.reset()
        assert len(engine._messages) == 0
        assert engine._turn_count == 0

    @pytest.mark.asyncio
    async def test_simple_turn(self):
        engine, mock_router = self._make_engine()

        result = await engine.run_turn("Hello")

        assert result.content == "Hello!"
        assert result.tool_calls_made == 0
        assert result.total_usage.total_tokens == 15
        assert engine._turn_count == 1
        assert len(engine._messages) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_turn_with_stream_callback(self):
        engine, _ = self._make_engine()

        streamed = []
        result = await engine.run_turn(
            "Hello",
            on_stream=lambda s: streamed.append(s),
        )

        assert len(streamed) == 1
        assert streamed[0] == "Hello!"

    @pytest.mark.asyncio
    async def test_turn_with_tool_call(self):
        engine, mock_router = self._make_engine()

        # 第一次调用返回 tool call
        tool_response = CompletionResponse(
            content="",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "echo", "arguments": '{"text": "hi"}'},
            }],
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="test",
            finish_reason="tool_calls",
        )

        # 第二次调用返回最终回复
        final_response = CompletionResponse(
            content="Done!",
            usage=Usage(prompt_tokens=20, completion_tokens=5, total_tokens=25),
            model="test",
            finish_reason="stop",
        )

        mock_router.complete_with_failover = AsyncMock(
            side_effect=[tool_response, final_response]
        )

        # 注册 tool
        async def echo_handler(text: str) -> str:
            return f"Echo: {text}"

        engine.register_tool(
            name="echo",
            description="Echo tool",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
            handler=echo_handler,
        )

        tool_calls = []
        result = await engine.run_turn(
            "Echo something",
            on_tool_call=lambda name, args: tool_calls.append(name),
        )

        assert result.content == "Done!"
        assert result.tool_calls_made == 1
        assert len(tool_calls) == 1
        assert tool_calls[0] == "echo"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        engine, _ = self._make_engine()
        result = await engine._execute_tool("nonexistent", "{}")
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_execute_tool_invalid_json(self):
        engine, _ = self._make_engine()

        async def handler(**kwargs):
            return "ok"

        engine.register_tool("t", "t", {"type": "object"}, handler)
        result = await engine._execute_tool("t", "not json")
        assert "Invalid JSON" in result
