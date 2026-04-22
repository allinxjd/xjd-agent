"""AI 模型 Provider 统一抽象基类.

定义统一的 Provider 接口，所有模型厂商实现此基类。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

class ProviderType(str, Enum):
    """支持的 AI Provider 类型."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    BEDROCK = "bedrock"
    OPENROUTER = "openrouter"
    ZHIPU = "zhipu"
    QWEN = "qwen"
    DOUBAO = "doubao"
    MINIMAX = "minimax"
    MOONSHOT = "moonshot"
    DEEPSEEK = "deepseek"
    MISTRAL = "mistral"
    OLLAMA = "ollama"
    CUSTOM = "custom"

@dataclass
class ModelInfo:
    """模型元数据."""

    provider: ProviderType
    model_id: str
    display_name: str = ""
    context_length: int = 128_000
    max_output_tokens: int = 4096
    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    supports_thinking: bool = False
    input_price_per_mtok: float = 0.0  # $/M tokens
    output_price_per_mtok: float = 0.0
    tier: str = "strong"  # "strong" | "cheap" | "reasoning"

    @property
    def full_id(self) -> str:
        return f"{self.provider.value}:{self.model_id}"

@dataclass
class Message:
    """统一消息格式."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | list[dict[str, Any]] = ""
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None

@dataclass
class ToolDefinition:
    """工具定义."""

    name: str
    description: str
    parameters: dict[str, Any]

@dataclass
class Usage:
    """Token 用量统计."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def estimated_cost(self) -> float:
        """粗略估算成本 (需要知道模型价格才准确)."""
        return 0.0

@dataclass
class CompletionResponse:
    """统一的 Completion 响应."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    finish_reason: str = ""  # "stop" | "tool_calls" | "length"
    thinking: Optional[str] = None  # 思考过程 (extended thinking)
    raw: Optional[Any] = None  # 原始响应

@dataclass
class StreamChunk:
    """流式输出块."""

    content: str = ""
    tool_calls_delta: Optional[list[dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    usage: Optional[Usage] = None
    thinking: Optional[str] = None

class BaseProvider(ABC):
    """AI 模型 Provider 统一基类.

    所有 Provider (OpenAI/Anthropic/Google/智谱/通义/豆包/DeepSeek...)
    都实现此接口，保证 Agent 核心可以无缝切换模型。
    """

    def __init__(self, provider_type: ProviderType, **kwargs: Any) -> None:
        self.provider_type = provider_type
        self._config = kwargs
        self._available_models: dict[str, ModelInfo] = {}

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 显示名称."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        model: str,
        tools: Optional[list[ToolDefinition]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        thinking: Optional[str] = None,  # "off"|"low"|"medium"|"high"
        api_key_override: Optional[str] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """发送请求并获取完整响应."""

    @abstractmethod
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
        yield StreamChunk()  # type: ignore

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """列出可用模型."""

    async def health_check(self) -> bool:
        """健康检查."""
        try:
            models = await self.list_models()
            return len(models) > 0
        except (ConnectionError, TimeoutError, OSError):
            return False

    def get_model_info(self, model_id: str) -> Optional[ModelInfo]:
        """获取模型元数据."""
        return self._available_models.get(model_id)


# ── Retry 工具 ──────────────────────────────────────────────

import asyncio
import random

async def retry_with_backoff(
    coro_factory,
    max_retries: int = 3,
    base_delay: float = 1.0,
    retryable_status: tuple = (429, 500, 502, 503, 529),
):
    """带指数退避的异步重试.

    Args:
        coro_factory: 无参 callable，每次调用返回新的 awaitable
        max_retries: 最大重试次数 (不含首次)
        base_delay: 基础延迟秒数
        retryable_status: 可重试的 HTTP 状态码
    """
    last_err = None
    for attempt in range(max_retries + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_err = e
            # 检查是否可重试
            status = getattr(e, 'status_code', None) or getattr(e, 'status', None)
            err_str = str(e).lower()
            is_retryable = (
                (status and status in retryable_status)
                or 'rate' in err_str
                or 'timeout' in err_str
                or 'overloaded' in err_str
                or 'connection' in err_str
                or 'connect' in err_str
                or '429' in err_str
                or '503' in err_str
            )
            if not is_retryable or attempt >= max_retries:
                raise
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "Provider 请求失败 (attempt %d/%d), %.1fs 后重试: %s",
                attempt + 1, max_retries + 1, delay, e,
            )
            await asyncio.sleep(delay)
    raise last_err  # type: ignore
