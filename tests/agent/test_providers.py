"""测试 — Provider 基础类 + 消息模型."""

from __future__ import annotations

import json
import pytest

from agent.providers.base import (
    CompletionResponse,
    Message,
    ModelInfo,
    ProviderType,
    StreamChunk,
    ToolDefinition,
    Usage,
)


class TestProviderType:
    def test_all_types_exist(self):
        assert ProviderType.OPENAI.value == "openai"
        assert ProviderType.ANTHROPIC.value == "anthropic"
        assert ProviderType.GOOGLE.value == "google"
        assert ProviderType.DEEPSEEK.value == "deepseek"

    def test_from_string(self):
        assert ProviderType("openai") == ProviderType.OPENAI
        assert ProviderType("deepseek") == ProviderType.DEEPSEEK


class TestMessage:
    def test_basic_message(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_assistant_with_tool_calls(self):
        tool_calls = [{
            "id": "call_123",
            "type": "function",
            "function": {"name": "test", "arguments": "{}"},
        }]
        msg = Message(role="assistant", content="", tool_calls=tool_calls)
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0]["function"]["name"] == "test"

    def test_tool_result(self):
        msg = Message(role="tool", content="result", tool_call_id="call_123", name="test")
        assert msg.role == "tool"
        assert msg.tool_call_id == "call_123"


class TestToolDefinition:
    def test_basic_tool(self):
        tool = ToolDefinition(
            name="web_search",
            description="搜索互联网",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        assert tool.name == "web_search"
        assert "query" in tool.parameters["properties"]


class TestUsage:
    def test_default_usage(self):
        usage = Usage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_usage_accumulation(self):
        u = Usage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert u.total_tokens == 150


class TestCompletionResponse:
    def test_basic_response(self):
        resp = CompletionResponse(
            content="Hello!",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="gpt-4o",
            finish_reason="stop",
        )
        assert resp.content == "Hello!"
        assert resp.model == "gpt-4o"
        # tool_calls defaults to empty list (not None)
        assert resp.tool_calls == []

    def test_response_with_tool_calls(self):
        resp = CompletionResponse(
            content="",
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {"name": "test", "arguments": '{"x": 1}'},
            }],
            usage=Usage(),
            model="gpt-4o",
            finish_reason="tool_calls",
        )
        assert len(resp.tool_calls) == 1
        assert resp.finish_reason == "tool_calls"


class TestModelInfo:
    def test_model_info(self):
        info = ModelInfo(
            provider=ProviderType.OPENAI,
            model_id="gpt-4o",
            display_name="GPT-4o",
            context_length=128000,
            max_output_tokens=4096,
            supports_tools=True,
            supports_vision=True,
            input_price_per_mtok=2.50,
            output_price_per_mtok=10.00,
            tier="strong",
        )
        assert info.model_id == "gpt-4o"
        assert info.context_length == 128000
        assert info.supports_tools is True


class TestStreamChunk:
    def test_text_chunk(self):
        chunk = StreamChunk(content="Hello")
        assert chunk.content == "Hello"
        assert chunk.tool_calls_delta is None  # actual field name

    def test_empty_chunk(self):
        chunk = StreamChunk()
        assert chunk.content == ""  # defaults to empty string


class TestOpenRouterHeaders:
    def test_openrouter_has_extra_headers(self):
        from agent.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider(
            provider_type=ProviderType.OPENROUTER,
            api_key="test-key",
        )
        assert provider._base_url == "https://openrouter.ai/api/v1"
        # 验证 client 有 default_headers
        headers = provider._client._custom_headers
        assert "HTTP-Referer" in headers or "http-referer" in {k.lower() for k in headers}

    def test_non_openrouter_no_extra_headers(self):
        from agent.providers.openai_provider import OpenAIProvider
        provider = OpenAIProvider(
            provider_type=ProviderType.DEEPSEEK,
            api_key="test-key",
        )
        headers = provider._client._custom_headers
        assert "HTTP-Referer" not in headers
