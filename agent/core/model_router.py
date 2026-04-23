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
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

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

if TYPE_CHECKING:
    from agent.core.credential_manager import CredentialManager

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

_IMMEDIATE_FAIL_CODES = {401, 403, 404, 429}
_CONNECTION_ERROR_TYPES = ("ConnectError", "ConnectionError", "Connection error", "connection attempts failed")
_CIRCUIT_BREAKER_COOLDOWN = 300.0

# 每个 provider 的默认模型 — 用于自动构建 failover chain
_DEFAULT_MODELS: dict[str, str] = {
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-20241022",
    "google": "gemini-2.0-flash",
    "zhipu": "glm-4-flash",
    "qwen": "qwen-turbo",
    "doubao": "doubao-1.5-pro-256k",
    "moonshot": "moonshot-v1-8k",
    "minimax": "MiniMax-Text-01",
}


def _extract_status_code(exc: Exception) -> int:
    """从异常中提取 HTTP 状态码."""
    code = getattr(exc, 'status_code', None) or getattr(exc, 'status', None)
    if isinstance(code, int):
        return code
    err_str = str(exc)
    for c in (401, 403, 404, 429, 500, 502, 503):
        if str(c) in err_str:
            return c
    return 0

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

    def __init__(self, credential_manager: Optional[CredentialManager] = None) -> None:
        self._providers: dict[str, BaseProvider] = {}
        self._credentials: dict[str, list[CredentialEntry]] = {}
        self._credential_mgr = credential_manager

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
        """设置主模型，并自动从已注册 provider 构建 failover chain.

        保留已有的手动 failover 条目 (add_failover / add_failover_from_config)，
        只追加尚未存在的自动 failover。
        """
        self._primary_provider = provider_name
        self._primary_model = model
        existing = {(p, m) for p, m in self._failover_chain}
        # 优先把 deepseek 排在 failover 前面
        sorted_providers = sorted(
            self._providers.items(),
            key=lambda x: (0 if x[0] == "deepseek" else 1),
        )
        for prov_name, prov in sorted_providers:
            if prov_name == provider_name:
                continue
            fallback_model = _DEFAULT_MODELS.get(prov_name)
            if fallback_model and (prov_name, fallback_model) not in existing:
                self._failover_chain.append((prov_name, fallback_model))
        # 同 provider 的默认模型也加入 failover（如 primary=glm-5.1 → failover glm-4-flash）
        default_for_primary = _DEFAULT_MODELS.get(provider_name)
        if default_for_primary and default_for_primary != model and (provider_name, default_for_primary) not in existing:
            self._failover_chain.append((provider_name, default_for_primary))
        if self._failover_chain:
            logger.info("Failover chain: %s", [(p, m) for p, m in self._failover_chain])

    def set_cheap(self, provider_name: str, model: str) -> None:
        """设置便宜模型 (简单问题自动降级)."""
        self._cheap_provider = provider_name
        self._cheap_model = model
        self._cheap_routing_enabled = True

    def add_failover(self, provider_name: str, model: str) -> None:
        """添加故障转移候选."""
        self._failover_chain.append((provider_name, model))

    def add_failover_from_config(self, failover_configs: list) -> None:
        """从配置加载 failover providers 并注册."""
        from agent.providers.openai_provider import OpenAIProvider
        from agent.providers.base import ProviderType
        for fc in failover_configs:
            if not fc.provider:
                continue
            api_key = fc.api_key or ""
            if not api_key and fc.provider == "ollama":
                api_key = "ollama"
            if not api_key:
                logger.warning("Failover %s:%s skipped — no API key", fc.provider, fc.model)
                continue
            if fc.provider not in self._providers:
                prov = OpenAIProvider(
                    provider_type=ProviderType(fc.provider),
                    api_key=api_key,
                    base_url=fc.base_url or None,
                )
                self.register_provider(prov)
            model = fc.model or _DEFAULT_MODELS.get(fc.provider, "")
            if model:
                self._failover_chain.append((fc.provider, model))
                logger.info("Failover added from config: %s:%s", fc.provider, model)

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

        # 3. 回退: 优先 deepseek，否则第一个可用 Provider
        providers_to_try = sorted(
            self._providers.items(),
            key=lambda x: (0 if x[0] == "deepseek" else 1),
        )
        for name, provider in providers_to_try:
            fallback_model = _DEFAULT_MODELS.get(name, "deepseek-chat")
            logger.warning("No primary model configured, falling back to provider: %s model: %s", name, fallback_model)
            return RouteDecision(
                provider=provider,
                model=fallback_model,
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
        for idx, (provider, model, reason) in enumerate(attempts):
            is_last = (idx == len(attempts) - 1)
            provider_key = f"{provider.name}:{model}"
            cooldown_until = self._provider_cooldown.get(provider_key, 0.0)
            if (
                not is_last
                and self._provider_failures.get(provider_key, 0) >= 3
                and time.time() < cooldown_until
            ):
                logger.warning(
                    "Circuit breaker: skipping %s (in cooldown until %.0fs from now)",
                    provider_key, cooldown_until - time.time(),
                )
                continue

            # Credential pool: 获取当前可用 key
            active_key = None
            if self._credential_mgr:
                active_key = self._credential_mgr.get_active_key(provider.name)

            try:
                logger.info(
                    "Attempting %s:%s (reason=%s)", provider.name, model, reason
                )
                response = await provider.complete(
                    messages=messages, model=model, tools=tools,
                    api_key_override=active_key, **kwargs,
                )
                if reason == "failover":
                    logger.warning(
                        "Failover succeeded: %s:%s (primary failed)", provider.name, model
                    )
                self._provider_failures.pop(provider_key, None)
                self._provider_cooldown.pop(provider_key, None)
                if active_key and self._credential_mgr:
                    self._credential_mgr.report_success(provider.name, active_key)
                return response
            except Exception as e:
                last_error = e
                status = _extract_status_code(e)
                err_str = str(e)

                if active_key and self._credential_mgr:
                    self._credential_mgr.report_error(provider.name, active_key, status)

                # 连接错误 → 立即跳到下一个 provider (HermesAgent 模式)
                is_conn_error = any(t in err_str for t in _CONNECTION_ERROR_TYPES)
                if is_conn_error:
                    logger.warning(
                        "Provider %s:%s connection error, skipping to next provider",
                        provider.name, model,
                    )
                    self._provider_failures[provider_key] = 3
                    self._provider_cooldown[provider_key] = time.time() + 60.0
                    continue

                if status in _IMMEDIATE_FAIL_CODES:
                    logger.error(
                        "Provider %s:%s auth/not-found error (HTTP %d), skipping immediately",
                        provider.name, model, status,
                    )
                    self._provider_failures[provider_key] = 3
                    self._provider_cooldown[provider_key] = time.time() + _CIRCUIT_BREAKER_COOLDOWN
                    continue

                self._provider_failures[provider_key] = self._provider_failures.get(provider_key, 0) + 1
                if self._provider_failures[provider_key] >= 3:
                    self._provider_cooldown[provider_key] = time.time() + _CIRCUIT_BREAKER_COOLDOWN
                logger.warning(
                    "Provider %s:%s failed (%d consecutive): %s, trying next...",
                    provider.name, model, self._provider_failures[provider_key], e,
                )
                continue

        err_str = str(last_error) if last_error else ""
        if "429" in err_str or "余额" in err_str or "quota" in err_str.lower():
            hint = "当前模型余额不足。请在 WebUI 设置中切换到其他模型，或充值后重试。"
        elif any(str(c) in err_str for c in (401, 403)):
            hint = "API Key 无效或已过期。请在 WebUI 设置中更新 API Key。"
        else:
            hint = "请检查网络连接，或在 WebUI 设置中配置备用模型。"
        raise RuntimeError(
            f"{hint}\n(原始错误: {last_error})"
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
        for idx, (provider, model) in enumerate(attempts):
            is_last = (idx == len(attempts) - 1)
            provider_key = f"{provider.name}:{model}"
            cooldown_until = self._provider_cooldown.get(provider_key, 0.0)
            if (
                not is_last
                and self._provider_failures.get(provider_key, 0) >= 3
                and time.time() < cooldown_until
            ):
                logger.warning(
                    "Circuit breaker (stream): skipping %s", provider_key,
                )
                continue

            active_key = None
            if self._credential_mgr:
                active_key = self._credential_mgr.get_active_key(provider.name)

            try:
                async for chunk in provider.stream(
                    messages=messages, model=model, tools=tools,
                    api_key_override=active_key, **kwargs,
                ):
                    yield chunk
                self._provider_failures.pop(provider_key, None)
                self._provider_cooldown.pop(provider_key, None)
                if active_key and self._credential_mgr:
                    self._credential_mgr.report_success(provider.name, active_key)
                return
            except Exception as e:
                last_error = e
                status = _extract_status_code(e)
                err_str = str(e)
                if active_key and self._credential_mgr:
                    self._credential_mgr.report_error(provider.name, active_key, status)
                is_conn_error = any(t in err_str for t in _CONNECTION_ERROR_TYPES)
                if is_conn_error:
                    self._provider_failures[provider_key] = 3
                    self._provider_cooldown[provider_key] = time.time() + 60.0
                elif status in _IMMEDIATE_FAIL_CODES:
                    self._provider_failures[provider_key] = 3
                    self._provider_cooldown[provider_key] = time.time() + _CIRCUIT_BREAKER_COOLDOWN
                else:
                    self._provider_failures[provider_key] = self._provider_failures.get(provider_key, 0) + 1
                    if self._provider_failures[provider_key] >= 3:
                        self._provider_cooldown[provider_key] = time.time() + _CIRCUIT_BREAKER_COOLDOWN
                logger.warning("Stream %s:%s failed: %s", provider.name, model, e)
                continue

        err_str = str(last_error) if last_error else ""
        if "429" in err_str or "余额" in err_str or "quota" in err_str.lower():
            hint = "当前模型余额不足。请在 WebUI 设置中切换到其他模型，或充值后重试。"
        elif any(str(c) in err_str for c in (401, 403)):
            hint = "API Key 无效或已过期。请在 WebUI 设置中更新 API Key。"
        else:
            hint = "请检查网络连接，或在 WebUI 设置中配置备用模型。"
        raise RuntimeError(f"{hint}\n(原始错误: {last_error})") from last_error


def build_credential_manager_from_config(config: Any) -> Optional["CredentialManager"]:
    """从 Config 构建 CredentialManager（如果有多 Key 配置）."""
    from agent.core.credential_manager import CredentialManager

    cm = CredentialManager()
    has_keys = False

    for pc in [config.model.primary, config.model.cheap] + (config.model.failover or []):
        if pc and pc.provider and pc.api_keys:
            cm.add_keys(pc.provider, pc.api_keys)
            has_keys = True
        if pc and pc.provider and pc.api_key:
            cm.add_key(pc.provider, pc.api_key)

    return cm if has_keys else None
