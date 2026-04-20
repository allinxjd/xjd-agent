"""智能模型路由器 — 核心能力:
1. cheap/strong 自动路由: 简单问题用便宜模型，复杂任务用强模型
2. 故障转移 (failover): 主模型失败自动切换备用模型
3. 凭证池 (credential pool): 多 API Key 轮换，避免限流
4. Provider 注册表: 统一管理所有 AI 模型 Provider
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
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
)

logger = logging.getLogger(__name__)

# 判断"简单问题"的关键词
_COMPLEX_KEYWORDS = {
    "debug", "debugging", "implement", "implementation", "refactor",
    "patch", "traceback", "stacktrace", "exception", "error",
    "analyze", "analysis", "investigate", "architecture", "design",
    "compare", "benchmark", "optimize", "review", "terminal",
    "shell", "tool", "tools", "pytest", "test", "tests", "plan",
    "planning", "delegate", "subagent", "cron", "docker", "kubernetes",
    "代码", "调试", "分析", "部署", "架构", "设计", "重构", "优化",
}

@dataclass
class CredentialEntry:
    """凭证条目."""

    api_key: str
    provider: ProviderType
    base_url: Optional[str] = None
    fail_count: int = 0
    last_fail_time: float = 0.0
    total_requests: int = 0
    is_disabled: bool = False

    @property
    def cooldown_remaining(self) -> float:
        """故障冷却剩余时间 (指数退避)."""
        if self.fail_count == 0:
            return 0.0
        cooldown = min(60 * (2 ** (self.fail_count - 1)), 600)  # 最长 10 分钟
        elapsed = time.time() - self.last_fail_time
        return max(0.0, cooldown - elapsed)

@dataclass
class RouteDecision:
    """路由决策."""

    provider: BaseProvider
    model: str
    reason: str  # "primary" | "cheap" | "failover"
    credential: Optional[CredentialEntry] = None

class ModelRouter:
    """智能模型路由器.

    用法:
        router = ModelRouter()
        router.register_provider(openai_provider)
        router.register_provider(anthropic_provider)
        router.set_primary("anthropic", "claude-sonnet-4-20250514")
        router.set_cheap("openai", "gpt-4o-mini")
        router.add_failover("openai", "gpt-4o")

        # 自动路由
        decision = router.route(user_message)
        response = await decision.provider.complete(messages, decision.model)
    """

    def __init__(self) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._credentials: dict[str, list[CredentialEntry]] = {}

        # 路由配置
        self._primary_provider: Optional[str] = None
        self._primary_model: Optional[str] = None
        self._cheap_provider: Optional[str] = None
        self._cheap_model: Optional[str] = None
        self._failover_chain: list[tuple[str, str]] = []  # [(provider, model), ...]

        # cheap 路由参数
        self._cheap_routing_enabled: bool = False
        self._max_simple_chars: int = 160
        self._max_simple_words: int = 28

        # Circuit breaker state
        self._provider_failures: dict[str, int] = {}
        self._provider_cooldown: dict[str, float] = {}  # provider_key -> cooldown_until timestamp

    def register_provider(self, provider: BaseProvider) -> None:
        """注册 Provider."""
        self._providers[provider.name] = provider
        logger.info("Registered provider: %s", provider.name)

    def get_provider(self, name: str) -> Optional[BaseProvider]:
        """获取 Provider."""
        return self._providers.get(name)

    def set_primary(self, provider_name: str, model: str) -> None:
        """设置主模型."""
        self._primary_provider = provider_name
        self._primary_model = model

    def set_cheap(self, provider_name: str, model: str) -> None:
        """设置便宜模型 (简单问题自动降级)."""
        self._cheap_provider = provider_name
        self._cheap_model = model
        self._cheap_routing_enabled = True

    def add_failover(self, provider_name: str, model: str) -> None:
        """添加故障转移候选."""
        self._failover_chain.append((provider_name, model))

    def _is_simple_message(self, text: str) -> bool:
        """判断是否是简单消息 (可用便宜模型处理)."""
        if not text.strip():
            return False

        # 长度检查
        if len(text) > self._max_simple_chars:
            return False
        if len(text.split()) > self._max_simple_words:
            return False

        # 多行 = 可能是代码
        if text.count("\n") > 1:
            return False

        # 含代码块
        if "```" in text or "`" in text:
            return False

        # 含 URL
        import re
        if re.search(r"https?://|www\.", text, re.IGNORECASE):
            return False

        # 复杂关键词
        lowered = text.lower()
        words = {w.strip(".,;:!?()[]{}\"'`") for w in lowered.split()}
        if words & _COMPLEX_KEYWORDS:
            return False

        return True

    def route(self, user_message: str = "") -> RouteDecision:
        """根据消息内容做路由决策."""
        # 1. 尝试 cheap 路由
        if (
            self._cheap_routing_enabled
            and self._cheap_provider
            and self._cheap_model
            and self._is_simple_message(user_message)
        ):
            provider = self._providers.get(self._cheap_provider)
            if provider:
                return RouteDecision(
                    provider=provider,
                    model=self._cheap_model,
                    reason="cheap",
                )

        # 2. 主模型
        if self._primary_provider and self._primary_model:
            provider = self._providers.get(self._primary_provider)
            if provider:
                return RouteDecision(
                    provider=provider,
                    model=self._primary_model,
                    reason="primary",
                )

        # 3. 回退到第一个可用 Provider (使用其默认模型)
        for name, provider in self._providers.items():
            logger.warning("No primary model configured, falling back to provider: %s", name)
            return RouteDecision(
                provider=provider,
                model=getattr(provider, "default_model", ""),
                reason="fallback",
            )

        raise RuntimeError("No providers registered. Run `xjd-agent model` to configure.")

    async def complete_with_failover(
        self,
        messages: list[Message],
        user_message: str = "",
        tools: Optional[list[ToolDefinition]] = None,
        **kwargs: Any,
    ) -> CompletionResponse:
        """带故障转移的 complete — 主模型失败自动切换."""
        decision = self.route(user_message)

        # 构建尝试链: [主模型] + failover chain
        attempts: list[tuple[BaseProvider, str, str]] = [
            (decision.provider, decision.model, decision.reason)
        ]
        for prov_name, model in self._failover_chain:
            prov = self._providers.get(prov_name)
            if prov and (prov_name != decision.provider.name or model != decision.model):
                attempts.append((prov, model, "failover"))

        last_error: Optional[Exception] = None
        for provider, model, reason in attempts:
            # Circuit breaker: skip providers in cooldown
            provider_key = f"{provider.name}:{model}"
            cooldown_until = self._provider_cooldown.get(provider_key, 0.0)
            if self._provider_failures.get(provider_key, 0) >= 3 and time.time() < cooldown_until:
                logger.warning(
                    "Circuit breaker: skipping %s (in cooldown until %.0fs from now)",
                    provider_key, cooldown_until - time.time(),
                )
                continue

            try:
                logger.info(
                    "Attempting %s:%s (reason=%s)", provider.name, model, reason
                )
                response = await provider.complete(
                    messages=messages, model=model, tools=tools, **kwargs
                )
                if reason == "failover":
                    logger.warning(
                        "Failover succeeded: %s:%s (primary failed)", provider.name, model
                    )
                # Reset circuit breaker on success
                self._provider_failures.pop(provider_key, None)
                self._provider_cooldown.pop(provider_key, None)
                return response
            except Exception as e:
                last_error = e
                # Track consecutive failures
                self._provider_failures[provider_key] = self._provider_failures.get(provider_key, 0) + 1
                if self._provider_failures[provider_key] >= 3:
                    self._provider_cooldown[provider_key] = time.time() + 60.0
                logger.warning(
                    "Provider %s:%s failed (%d consecutive): %s, trying next...",
                    provider.name, model, self._provider_failures[provider_key], e,
                )
                continue

        raise RuntimeError(
            f"All providers failed. Last error: {last_error}"
        ) from last_error

    async def stream_with_failover(
        self,
        messages: list[Message],
        user_message: str = "",
        tools: Optional[list[ToolDefinition]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        """带故障转移的 stream."""
        decision = self.route(user_message)

        attempts: list[tuple[BaseProvider, str]] = [
            (decision.provider, decision.model)
        ]
        for prov_name, model in self._failover_chain:
            prov = self._providers.get(prov_name)
            if prov:
                attempts.append((prov, model))

        last_error: Optional[Exception] = None
        for provider, model in attempts:
            try:
                async for chunk in provider.stream(
                    messages=messages, model=model, tools=tools, **kwargs
                ):
                    yield chunk
                return
            except Exception as e:
                last_error = e
                logger.warning("Stream %s:%s failed: %s", provider.name, model, e)
                continue

        raise RuntimeError(f"All stream providers failed: {last_error}") from last_error
