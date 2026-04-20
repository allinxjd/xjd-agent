"""事件钩子系统 — Hooks + Webhooks.

支持:
- 事件注册与触发 (before/after 生命周期)
- 内置事件: on_message, on_tool_call, on_response, on_error, on_turn_start, on_turn_end
- Webhook 入站: 外部 HTTP POST 触发 agent 动作
- 钩子优先级排序
- 异步执行

"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

class HookEvent(str, Enum):
    """内置事件类型."""

    ON_MESSAGE = "on_message"
    ON_TOOL_CALL = "on_tool_call"
    ON_TOOL_RESULT = "on_tool_result"
    ON_RESPONSE = "on_response"
    ON_ERROR = "on_error"
    ON_TURN_START = "on_turn_start"
    ON_TURN_END = "on_turn_end"
    ON_MEMORY_STORE = "on_memory_store"
    ON_SKILL_LEARN = "on_skill_learn"
    ON_WEBHOOK = "on_webhook"

class HookPhase(str, Enum):
    """钩子阶段."""

    BEFORE = "before"
    AFTER = "after"

# 钩子回调类型: async def handler(event, data) -> Optional[data]
HookHandler = Callable[[str, dict], Coroutine[Any, Any, Optional[dict]]]

@dataclass
class HookRegistration:
    """钩子注册信息."""

    event: str
    phase: HookPhase
    handler: HookHandler
    priority: int = 0  # 越小越先执行
    name: str = ""
    enabled: bool = True

@dataclass
class WebhookConfig:
    """Webhook 配置."""

    path: str = ""  # URL 路径, 如 /webhook/github
    secret: str = ""  # 验证密钥
    events: list[str] = field(default_factory=list)  # 触发的事件
    transform: Optional[Callable] = None  # 请求体转换函数

class HookManager:
    """事件钩子管理器."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookRegistration]] = {}
        self._webhooks: dict[str, WebhookConfig] = {}
        self._history: list[dict] = []
        self._max_history = 100

    def register(
        self,
        event: str,
        handler: HookHandler,
        phase: HookPhase = HookPhase.AFTER,
        priority: int = 0,
        name: str = "",
    ) -> None:
        """注册钩子."""
        key = f"{phase.value}:{event}"
        if key not in self._hooks:
            self._hooks[key] = []

        reg = HookRegistration(
            event=event, phase=phase, handler=handler,
            priority=priority, name=name or handler.__name__,
        )
        self._hooks[key].append(reg)
        self._hooks[key].sort(key=lambda h: h.priority)
        logger.debug("注册钩子: %s %s (priority=%d)", phase.value, event, priority)

    def unregister(self, event: str, name: str, phase: HookPhase = HookPhase.AFTER) -> bool:
        """取消注册钩子."""
        key = f"{phase.value}:{event}"
        hooks = self._hooks.get(key, [])
        before = len(hooks)
        self._hooks[key] = [h for h in hooks if h.name != name]
        return len(self._hooks[key]) < before

    async def trigger(
        self,
        event: str,
        data: Optional[dict] = None,
        phase: HookPhase = HookPhase.AFTER,
    ) -> dict:
        """触发事件，按优先级执行所有钩子."""
        key = f"{phase.value}:{event}"
        hooks = self._hooks.get(key, [])
        result = data or {}

        for hook in hooks:
            if not hook.enabled:
                continue
            try:
                modified = await hook.handler(event, result)
                if modified is not None:
                    result = modified
            except Exception as e:
                logger.error("钩子 %s 执行失败: %s", hook.name, e)

        # 记录历史
        self._history.append({
            "event": event, "phase": phase.value,
            "hooks_run": len([h for h in hooks if h.enabled]),
            "timestamp": time.time(),
        })
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return result

    def register_webhook(self, path: str, config: WebhookConfig) -> None:
        """注册 Webhook 端点."""
        config.path = path
        self._webhooks[path] = config
        logger.info("注册 Webhook: %s", path)

    async def handle_webhook(self, path: str, body: dict, headers: Optional[dict] = None) -> dict:
        """处理入站 Webhook 请求."""
        config = self._webhooks.get(path)
        if not config:
            return {"error": f"未知 webhook 路径: {path}"}

        # 验证密钥
        if config.secret and headers:
            sig = headers.get("x-webhook-signature", "")
            if not self._verify_signature(body, config.secret, sig):
                return {"error": "签名验证失败"}

        # 转换请求体
        event_data = body
        if config.transform:
            event_data = config.transform(body)

        # 触发关联事件
        results = {}
        for event in config.events or [HookEvent.ON_WEBHOOK.value]:
            result = await self.trigger(event, {"webhook_path": path, **event_data})
            results[event] = result

        return {"status": "ok", "events_triggered": list(results.keys())}

    def _verify_signature(self, body: dict, secret: str, signature: str) -> bool:
        """验证 Webhook 签名 (HMAC-SHA256)."""
        import hashlib
        import hmac
        import json as json_mod
        payload = json_mod.dumps(body, sort_keys=True).encode()
        expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def list_hooks(self) -> list[dict]:
        """列出所有已注册的钩子."""
        result = []
        for key, hooks in self._hooks.items():
            for h in hooks:
                result.append({
                    "event": h.event, "phase": h.phase.value,
                    "name": h.name, "priority": h.priority,
                    "enabled": h.enabled,
                })
        return result

    def list_webhooks(self) -> list[dict]:
        """列出所有 Webhook."""
        return [
            {"path": c.path, "events": c.events, "has_secret": bool(c.secret)}
            for c in self._webhooks.values()
        ]

    def get_history(self, limit: int = 20) -> list[dict]:
        """获取最近的事件触发历史."""
        return self._history[-limit:]
