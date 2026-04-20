"""Google Gemini Provider — 原生 Google AI API.

支持:
- Gemini 2.0 Flash / Pro
- Gemini 1.5 Flash / Pro
- 原生工具调用 (Function Calling)
- 多模态 (Vision)
- Grounding (Google Search)

依赖: google-genai (已在 core 依赖中)
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional

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

# 预置 Gemini 模型信息
GEMINI_MODELS: dict[str, ModelInfo] = {
    "gemini-2.0-flash": ModelInfo(
        provider=ProviderType.GOOGLE,
        model_id="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash",
        context_length=1_000_000,
        max_output_tokens=8192,
        supports_tools=True,
        supports_vision=True,
        supports_streaming=True,
        supports_thinking=True,
        input_price_per_mtok=0.075,
        output_price_per_mtok=0.30,
        tier="strong",
    ),
    "gemini-2.0-flash-lite": ModelInfo(
        provider=ProviderType.GOOGLE,
        model_id="gemini-2.0-flash-lite",
        display_name="Gemini 2.0 Flash Lite",
        context_length=1_000_000,
        max_output_tokens=8192,
        supports_tools=True,
        supports_vision=True,
        input_price_per_mtok=0.0,
        output_price_per_mtok=0.0,
        tier="cheap",
    ),
    "gemini-1.5-pro": ModelInfo(
        provider=ProviderType.GOOGLE,
        model_id="gemini-1.5-pro",
        display_name="Gemini 1.5 Pro",
        context_length=2_000_000,
        max_output_tokens=8192,
        supports_tools=True,
        supports_vision=True,
        supports_streaming=True,
        input_price_per_mtok=1.25,
        output_price_per_mtok=5.00,
        tier="strong",
    ),
    "gemini-1.5-flash": ModelInfo(
        provider=ProviderType.GOOGLE,
        model_id="gemini-1.5-flash",
        display_name="Gemini 1.5 Flash",
        context_length=1_000_000,
        max_output_tokens=8192,
        supports_tools=True,
        supports_vision=True,
        supports_streaming=True,
        input_price_per_mtok=0.075,
        output_price_per_mtok=0.30,
        tier="cheap",
    ),
}

class GoogleProvider(BaseProvider):
    """Google Gemini Provider — 原生 Google AI API.

    使用 google-genai SDK (官方推荐) 而非 REST API。

    配置:
        provider = GoogleProvider(api_key="AIza...")
    """

    def __init__(
        self,
        api_key: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(ProviderType.GOOGLE, **kwargs)
        self._api_key = api_key
        self._client = None
        self._available_models = dict(GEMINI_MODELS)

    @property
    def name(self) -> str:
        return "google"

    def _ensure_client(self) -> Any:
        """延迟初始化客户端."""
        if self._client is None:
            try:
                from google import genai
                self._client = genai.Client(api_key=self._api_key)
            except ImportError:
                raise ImportError("google-genai 未安装。请运行: pip install google-genai")
        return self._client

    def _convert_messages(self, messages: list[Message]) -> tuple[Optional[str], list[dict]]:
        """转换消息格式为 Gemini 格式.

        Gemini 的 system prompt 是单独的参数，不在 contents 中。
        """
        system_prompt = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content if isinstance(msg.content, str) else str(msg.content)
                continue

            role = "model" if msg.role == "assistant" else "user"
            content = msg.content if isinstance(msg.content, str) else str(msg.content)

            if msg.role == "tool":
                # Gemini 的 tool result 格式
                contents.append({
                    "role": "user",
                    "parts": [{
                        "function_response": {
                            "name": msg.name or "tool",
                            "response": {"result": content},
                        }
                    }],
                })
            elif msg.tool_calls:
                # Assistant 的 tool call
                parts = []
                if content:
                    parts.append({"text": content})
                for tc in msg.tool_calls:
                    import json
                    args = tc["function"]["arguments"]
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"raw": args}
                    parts.append({
                        "function_call": {
                            "name": tc["function"]["name"],
                            "args": args,
                        }
                    })
                contents.append({"role": "model", "parts": parts})
            else:
                contents.append({
                    "role": role,
                    "parts": [{"text": content}],
                })

        return system_prompt, contents

    def _convert_tools(self, tools: Optional[list[ToolDefinition]]) -> Optional[list[dict]]:
        """转换工具定义为 Gemini 格式."""
        if not tools:
            return None

        declarations = []
        for t in tools:
            decl = {
                "name": t.name,
                "description": t.description,
            }
            # 转换 parameters
            if t.parameters:
                params = dict(t.parameters)
                # Gemini 不支持 "required" 在 properties 级别
                decl["parameters"] = params
            declarations.append(decl)

        return [{"function_declarations": declarations}]

    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """调用 Gemini API."""
        client = self._ensure_client()
        system_prompt, contents = self._convert_messages(messages)

        config: dict[str, Any] = {
            "temperature": temperature,
        }
        if max_tokens:
            config["max_output_tokens"] = max_tokens
        if system_prompt:
            config["system_instruction"] = system_prompt

        gemini_tools = self._convert_tools(tools)
        if gemini_tools:
            config["tools"] = gemini_tools

        try:
            import asyncio
            response = await retry_with_backoff(
                lambda: asyncio.to_thread(
                    client.models.generate_content,
                    model=model, contents=contents, config=config,
                )
            )

            # 解析响应
            content = ""
            tool_calls = []

            if response.candidates:
                candidate = response.candidates[0]
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        content += part.text
                    elif hasattr(part, "function_call") and part.function_call:
                        import json
                        fc = part.function_call
                        tool_calls.append({
                            "id": f"call_{fc.name}_{len(tool_calls)}",
                            "type": "function",
                            "function": {
                                "name": fc.name,
                                "arguments": json.dumps(dict(fc.args), ensure_ascii=False),
                            },
                        })

            # 解析用量
            usage = Usage()
            if response.usage_metadata:
                um = response.usage_metadata
                usage.prompt_tokens = getattr(um, "prompt_token_count", 0) or 0
                usage.completion_tokens = getattr(um, "candidates_token_count", 0) or 0
                usage.total_tokens = getattr(um, "total_token_count", 0) or 0

            finish_reason = "tool_calls" if tool_calls else "stop"

            return CompletionResponse(
                content=content,
                tool_calls=tool_calls,
                usage=usage,
                model=model,
                finish_reason=finish_reason,
                raw=response,
            )

        except (ConnectionError, TimeoutError, ValueError, OSError) as e:
            logger.error("Gemini API error: %s", e, exc_info=True)
            raise

    async def stream(
        self,
        messages: list[Message],
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """流式输出."""
        client = self._ensure_client()
        system_prompt, contents = self._convert_messages(messages)

        config: dict[str, Any] = {
            "temperature": temperature,
        }
        if max_tokens:
            config["max_output_tokens"] = max_tokens
        if system_prompt:
            config["system_instruction"] = system_prompt

        try:
            import asyncio
            response = await retry_with_backoff(
                lambda: asyncio.to_thread(
                    client.models.generate_content_stream,
                    model=model, contents=contents, config=config,
                )
            )

            for chunk in response:
                if chunk.candidates:
                    for part in chunk.candidates[0].content.parts:
                        if hasattr(part, "text") and part.text:
                            yield StreamChunk(content=part.text)

        except (ConnectionError, TimeoutError, ValueError, OSError) as e:
            logger.error("Gemini stream error: %s", e)
            raise

    async def list_models(self) -> list[ModelInfo]:
        """列出可用模型."""
        return list(GEMINI_MODELS.values())
