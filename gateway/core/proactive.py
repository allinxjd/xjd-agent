"""主动通知系统 — Proactive Notifications.

Agent 主动发起联系用户:
- 定时提醒 (基于 cron 表达式)
- 事件触发通知 (heartbeat 异常、任务完成、预算告警)
- 多渠道推送 (复用 platform adapter)
- 通知模板
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

class NotificationType(str, Enum):
    SCHEDULED = "scheduled"      # 定时通知
    EVENT = "event"              # 事件触发
    REMINDER = "reminder"        # 提醒
    ALERT = "alert"              # 告警
    REPORT = "report"            # 报告

@dataclass
class Notification:
    """通知."""

    notification_id: str = ""
    type: NotificationType = NotificationType.EVENT
    title: str = ""
    message: str = ""
    channel: str = ""  # 目标渠道 (telegram/discord/email/...)
    recipient: str = ""  # 接收者 ID
    metadata: dict = field(default_factory=dict)
    created_at: float = 0.0
    sent_at: float = 0.0
    delivered: bool = False

@dataclass
class NotificationRule:
    """通知规则."""

    name: str = ""
    event: str = ""  # 触发事件
    channels: list[str] = field(default_factory=list)
    template: str = ""  # 消息模板
    cooldown_sec: float = 0.0  # 冷却时间
    enabled: bool = True
    _last_fired: float = 0.0

# 发送回调: async def send(channel, recipient, message) -> bool
SendCallback = Callable[[str, str, str], Coroutine[Any, Any, bool]]

class ProactiveNotifier:
    """主动通知管理器."""

    def __init__(self) -> None:
        self._rules: dict[str, NotificationRule] = {}
        self._send_callbacks: dict[str, SendCallback] = {}
        self._history: list[Notification] = []
        self._max_history = 200
        self._counter = 0

    def register_channel(self, channel: str, send_fn: SendCallback) -> None:
        """注册发送渠道."""
        self._send_callbacks[channel] = send_fn
        logger.info("注册通知渠道: %s", channel)

    def add_rule(self, rule: NotificationRule) -> None:
        """添加通知规则."""
        self._rules[rule.name] = rule

    def remove_rule(self, name: str) -> bool:
        """移除通知规则."""
        return self._rules.pop(name, None) is not None

    async def notify(
        self,
        event: str,
        data: Optional[dict] = None,
    ) -> list[Notification]:
        """根据事件触发通知."""
        sent = []
        now = time.time()

        for rule in self._rules.values():
            if not rule.enabled or rule.event != event:
                continue

            # 冷却检查
            if rule.cooldown_sec > 0 and (now - rule._last_fired) < rule.cooldown_sec:
                continue

            # 渲染消息
            message = self._render_template(rule.template, data or {})

            # 发送到所有渠道
            for channel in rule.channels:
                notif = await self._send(channel, "", message, NotificationType.EVENT)
                if notif:
                    sent.append(notif)

            rule._last_fired = now

        return sent

    async def send_direct(
        self,
        channel: str,
        recipient: str,
        message: str,
        ntype: NotificationType = NotificationType.EVENT,
    ) -> Optional[Notification]:
        """直接发送通知."""
        return await self._send(channel, recipient, message, ntype)

    async def _send(
        self,
        channel: str,
        recipient: str,
        message: str,
        ntype: NotificationType,
    ) -> Optional[Notification]:
        """内部发送."""
        self._counter += 1
        notif = Notification(
            notification_id=f"notif-{self._counter}",
            type=ntype,
            channel=channel,
            recipient=recipient,
            message=message,
            created_at=time.time(),
        )

        send_fn = self._send_callbacks.get(channel)
        if not send_fn:
            logger.warning("未注册的通知渠道: %s", channel)
            return notif

        try:
            ok = await send_fn(channel, recipient, message)
            notif.delivered = ok
            notif.sent_at = time.time()
        except Exception as e:
            logger.error("发送通知失败 (%s): %s", channel, e)
            notif.delivered = False

        self._history.append(notif)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        return notif

    def _render_template(self, template: str, data: dict) -> str:
        """渲染消息模板."""
        result = template
        for key, value in data.items():
            result = result.replace(f"{{{key}}}", str(value))
        return result

    def list_rules(self) -> list[dict]:
        """列出所有规则."""
        return [
            {"name": r.name, "event": r.event, "channels": r.channels, "enabled": r.enabled}
            for r in self._rules.values()
        ]

    def get_history(self, limit: int = 20) -> list[Notification]:
        """获取通知历史."""
        return self._history[-limit:]

    def get_stats(self) -> dict:
        """获取统计."""
        delivered = sum(1 for n in self._history if n.delivered)
        return {
            "total_sent": len(self._history),
            "delivered": delivered,
            "failed": len(self._history) - delivered,
            "rules": len(self._rules),
            "channels": list(self._send_callbacks.keys()),
        }
