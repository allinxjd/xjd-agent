"""OpenAI 兼容 Provider.

支持 OpenAI 官方 + 所有 OpenAI 兼容 API:
- OpenAI (GPT/o 系列)
- DeepSeek
- 通义千问 (dashscope)
- 豆包 (火山引擎)
- MiniMax
- Moonshot/Kimi
- 智谱 GLM
- OpenRouter (200+ 模型)
- Ollama (本地模型)
- 任意 OpenAI 兼容端点
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

from openai import AsyncOpenAI

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

# 预置的 OpenAI 兼容服务端点
KNOWN_ENDPOINTS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "minimax": "https://api.minimax.chat/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "siliconflow": "https://api.siliconflow.cn/v1",
}

_DEEPSEEK_V4_MODELS = {"deepseek-v4-pro", "deepseek-v4-flash", "deepseek-reasoner"}

class OpenAIProvider(BaseProvider):
    """OpenAI 兼容 Provider — 统一接入所有 OpenAI 兼容 API."""

    def __init__(
        self,
        provider_type: ProviderType = ProviderType.OPENAI,
        api_key: str = "",
        base_url: Optional[str] = None,
        organization: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(provider_type, **kwargs)
        self._api_key = api_key

        # 自动匹配端点
        if base_url:
            self._base_url = base_url
        else:
            self._base_url = KNOWN_ENDPOINTS.get(
                provider_type.value, "https://api.openai.com/v1"
            )

        # OpenRouter 需要额外 headers
        extra_headers = {}
        if provider_type == ProviderType.OPENROUTER:
            extra_headers = {
                "HTTP-Referer": "https://github.com/xjd-agent",
                "X-Title": "XJD Agent",
            }

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self._base_url,
            organization=organization,
            default_headers=extra_headers or None,
            timeout=120.0,
            max_retries=0,
        )

    @property
    def name(self) -> str:
        return self.provider_type.value

    def _convert_messages(self, messages: list[Message], include_reasoning: bool = False) -> list[dict[str, Any]]:
        """转换为 OpenAI 消息格式."""
        result = []
        for msg in messages:
            item: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.name:
                item["name"] = msg.name
            if msg.tool_call_id:
                item["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                item["tool_calls"] = msg.tool_calls
            if include_reasoning and msg.reasoning_content:
                item["reasoning_content"] = msg.reasoning_content
            result.append(item)
        return result

    def _convert_tools(
        self, tools: Optional[list[ToolDefinition]]
    ) -> Optional[list[dict[str, Any]]]:
        """转换为 OpenAI 工具格式."""
        if not tools:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def _parse_usage(self, usage: Any) -> Usage:
        """解析用量."""
        if not usage:
            return Usage()
        return Usage(
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            total_tokens=getattr(usage, "total_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "prompt_tokens_details", None)
            and getattr(usage.prompt_tokens_details, "cached_tokens", 0)
            or 0,
        )

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
        """发送请求并获取完整响应."""
        is_v4 = model in _DEEPSEEK_V4_MODELS
        params: dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages, include_reasoning=is_v4),
        }
        if not is_v4:
            params["temperature"] = temperature
        if max_tokens:
            params["max_tokens"] = max_tokens
        if tools:
            params["tools"] = self._convert_tools(tools)
            if not is_v4:
                params["tool_choice"] = kwargs.pop("tool_choice", None) or "auto"
        if is_v4:
            params["extra_body"] = {"thinking": {"type": "enabled"}}
            params["reasoning_effort"] = "high"

        client = self._client
        if api_key_override:
            client = AsyncOpenAI(
                api_key=api_key_override,
                base_url=self._base_url,
                timeout=120.0,
                max_retries=0,
            )

        response = await retry_with_backoff(
            lambda: client.chat.completions.create(**params)
        )

        choice = response.choices[0]
        content = choice.message.content or ""
        reasoning = getattr(choice.message, "reasoning_content", None) or ""
        if reasoning:
            logger.debug("V4 reasoning: %d chars", len(reasoning))
        logger.debug("API response: finish_reason=%s, has_tool_calls=%s, content_len=%d",
                     choice.finish_reason, bool(choice.message.tool_calls),
                     len(content))
        tool_calls_data = []
        if choice.message.tool_calls:
            tool_calls_data = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.message.tool_calls
            ]

        return CompletionResponse(
            content=content,
            tool_calls=tool_calls_data,
            usage=self._parse_usage(response.usage),
            model=response.model,
            finish_reason=choice.finish_reason or "stop",
            reasoning_content=reasoning or None,
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
        is_v4 = model in _DEEPSEEK_V4_MODELS
        params: dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(messages, include_reasoning=is_v4),
            "stream": True,
        }
        if not is_v4:
            params["temperature"] = temperature
        if max_tokens:
            params["max_tokens"] = max_tokens
        if tools:
            params["tools"] = self._convert_tools(tools)
            if not is_v4:
                params["tool_choice"] = kwargs.pop("tool_choice", None) or "auto"
        if is_v4:
            params["extra_body"] = {"thinking": {"type": "enabled"}}
            params["reasoning_effort"] = "high"

        client = self._client
        if api_key_override:
            client = AsyncOpenAI(
                api_key=api_key_override,
                base_url=self._base_url,
                timeout=120.0,
                max_retries=0,
            )

        stream = await retry_with_backoff(
            lambda: client.chat.completions.create(**params)
        )

        async for chunk in stream:  # type: ignore
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            yield StreamChunk(
                content=delta.content or "",
                finish_reason=chunk.choices[0].finish_reason,
            )

    async def list_models(self) -> list[ModelInfo]:
        """列出可用模型."""
        try:
            response = await self._client.models.list()
            models = []
            for m in response.data:
                info = ModelInfo(
                    provider=self.provider_type,
                    model_id=m.id,
                    display_name=m.id,
                )
                models.append(info)
                self._available_models[m.id] = info
            return models
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.warning("Failed to list models for %s: %s", self.name, e)
            return []
