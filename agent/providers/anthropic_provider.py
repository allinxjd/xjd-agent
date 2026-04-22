"""Anthropic Claude Provider.

原生 Anthropic SDK 接入，支持:
- Claude Opus / Sonnet / Haiku
- Extended Thinking
- Prompt Caching (自动 cache_control)
- Vision (图片输入)
- Tool Use
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

from anthropic import AsyncAnthropic

from agent.providers.base import (
    BaseProvider,
    CompletionResponse,
    Message,
    ModelInfo,
    ProviderType,
    StreamChunk,
    ToolDefinition,
    Usage,
    retry_with_backoff,
)

logger = logging.getLogger(__name__)

# Claude 模型目录
CLAUDE_MODELS: dict[str, ModelInfo] = {
    "claude-opus-4-20250514": ModelInfo(
        provider=ProviderType.ANTHROPIC,
        model_id="claude-opus-4-20250514",
        display_name="Claude Opus 4",
        context_length=200_000,
        max_output_tokens=32_000,
        supports_thinking=True,
        supports_vision=True,
        input_price_per_mtok=15.0,
        output_price_per_mtok=75.0,
        tier="strong",
    ),
    "claude-sonnet-4-20250514": ModelInfo(
        provider=ProviderType.ANTHROPIC,
        model_id="claude-sonnet-4-20250514",
        display_name="Claude Sonnet 4",
        context_length=200_000,
        max_output_tokens=16_000,
        supports_thinking=True,
        supports_vision=True,
        input_price_per_mtok=3.0,
        output_price_per_mtok=15.0,
        tier="strong",
    ),
    "claude-haiku-3-5-20241022": ModelInfo(
        provider=ProviderType.ANTHROPIC,
        model_id="claude-3-5-haiku-20241022",
        display_name="Claude 3.5 Haiku",
        context_length=200_000,
        max_output_tokens=8_192,
        supports_vision=True,
        input_price_per_mtok=0.8,
        output_price_per_mtok=4.0,
        tier="cheap",
    ),
}

class AnthropicProvider(BaseProvider):
    """Anthropic Claude 原生 Provider."""

    def __init__(
        self,
        api_key: str = "",
        base_url: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(ProviderType.ANTHROPIC, **kwargs)
        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=120.0,
        )
        self._available_models = dict(CLAUDE_MODELS)

    @property
    def name(self) -> str:
        return "anthropic"

    def _convert_messages(
        self, messages: list[Message]
    ) -> tuple[Optional[str], list[dict[str, Any]]]:
        """转换消息，分离 system prompt."""
        system_prompt = None
        converted = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content if isinstance(msg.content, str) else ""
                continue

            item: dict[str, Any] = {"role": msg.role, "content": msg.content}

            if msg.role == "tool":
                # Anthropic 格式: tool_result
                item = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content,
                        }
                    ],
                }

            if msg.tool_calls:
                # Assistant 的 tool_use
                content_blocks = []
                if msg.content:
                    content_blocks.append({"type": "text", "text": msg.content})
                for tc in msg.tool_calls:
                    import json

                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": tc["function"]["name"],
                            "input": json.loads(tc["function"]["arguments"]),
                        }
                    )
                item = {"role": "assistant", "content": content_blocks}

            converted.append(item)

        return system_prompt, converted

    def _convert_tools(
        self, tools: Optional[list[ToolDefinition]]
    ) -> Optional[list[dict[str, Any]]]:
        """转换为 Anthropic 工具格式."""
        if not tools:
            return None
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        api_key_override: Optional[str] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """发送请求."""
        system_prompt, converted = self._convert_messages(messages)
        model_info = self._available_models.get(model)

        params: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens or (model_info.max_output_tokens if model_info else 4096),
        }
        if system_prompt:
            params["system"] = system_prompt
        if temperature and thinking != "high":
            params["temperature"] = temperature

        tool_defs = self._convert_tools(tools)
        if tool_defs:
            params["tools"] = tool_defs

        # Extended thinking
        if thinking and thinking != "off":
            budget_map = {"low": 2048, "medium": 8192, "high": 32000}
            params["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_map.get(thinking, 8192),
            }

        client = self._client
        if api_key_override:
            client = AsyncAnthropic(
                api_key=api_key_override,
                base_url=self._client.base_url,
                timeout=120.0,
            )

        response = await retry_with_backoff(
            lambda: client.messages.create(**params)
        )

        # 解析响应
        content_text = ""
        thinking_text = ""
        tool_calls_data = []

        for block in response.content:
            if block.type == "text":
                content_text += block.text
            elif block.type == "thinking":
                thinking_text += block.text
            elif block.type == "tool_use":
                import json

                tool_calls_data.append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input),
                        },
                    }
                )

        usage = Usage(
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
        )

        return CompletionResponse(
            content=content_text,
            tool_calls=tool_calls_data,
            usage=usage,
            model=response.model,
            finish_reason="tool_calls" if tool_calls_data else "stop",
            thinking=thinking_text or None,
            raw=response,
        )

    async def stream(
        self,
        messages: list[Message],
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        api_key_override: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """流式输出."""
        system_prompt, converted = self._convert_messages(messages)
        model_info = self._available_models.get(model)

        params: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens or (model_info.max_output_tokens if model_info else 4096),
            "stream": True,
        }
        if system_prompt:
            params["system"] = system_prompt
        if temperature and thinking != "high":
            params["temperature"] = temperature

        tool_defs = self._convert_tools(tools)
        if tool_defs:
            params["tools"] = tool_defs

        client = self._client
        if api_key_override:
            client = AsyncAnthropic(
                api_key=api_key_override,
                base_url=self._client.base_url,
                timeout=120.0,
            )

        async with client.messages.stream(**params) as stream:
            async for event in stream:
                if hasattr(event, "type"):
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if hasattr(delta, "text"):
                            yield StreamChunk(content=delta.text)
                        elif hasattr(delta, "thinking"):
                            yield StreamChunk(thinking=delta.thinking)

    async def list_models(self) -> list[ModelInfo]:
        """列出 Claude 模型."""
        return list(self._available_models.values())
