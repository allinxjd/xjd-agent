"""FeishuBridge — 飞书双向桥接，每个角色绑定独立飞书 Bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.company.environment import CompanyEnvironment
    from agent.company.message import CompanyMessage

logger = logging.getLogger(__name__)


@dataclass
class FeishuBotConfig:
    """单个飞书 Bot 配置."""

    app_id: str
    app_secret: str
    role_name: str
    verification_token: str = ""
    encrypt_key: str = ""


class FeishuBridge:
    """飞书群 ↔ CompanyEnvironment 双向桥接.

    每个 CompanyRole 绑定一个独立飞书自建应用（Bot），
    所有 Bot 在同一飞书群中发言，模拟多人协作。
    """

    def __init__(
        self,
        group_chat_id: str,
        bot_configs: list[FeishuBotConfig],
        environment: Optional[CompanyEnvironment] = None,
    ) -> None:
        self._group_chat_id = group_chat_id
        self._environment = environment
        self._adapters: dict[str, Any] = {}
        self._bot_configs = {c.role_name: c for c in bot_configs}
        self._started = False
        self._seen_msg_ids: dict[str, bool] = {}

    def set_environment(self, env: CompanyEnvironment) -> None:
        self._environment = env

    async def start(self) -> None:
        """为每个角色创建 FeishuAdapter 实例."""
        from gateway.platforms.feishu import FeishuAdapter

        for role_name, cfg in self._bot_configs.items():
            adapter = FeishuAdapter({
                "app_id": cfg.app_id,
                "app_secret": cfg.app_secret,
                "verification_token": cfg.verification_token,
                "encrypt_key": cfg.encrypt_key,
                "mode": "long_poll",
                "accept_group_no_mention": True,
            })
            try:
                await adapter.start()
                self._adapters[role_name] = adapter
                logger.info("飞书 Bot 启动: %s (app=%s)", role_name, cfg.app_id)
            except Exception as e:
                logger.error("飞书 Bot 启动失败: %s — %s", role_name, e)

        if self._adapters:
            self._started = True
            for adapter in self._adapters.values():
                adapter.on_message(self._on_feishu_message)
            logger.info("飞书桥接已启动，群: %s，Bot 数: %d",
                        self._group_chat_id, len(self._adapters))

    async def stop(self) -> None:
        """停止所有飞书 Bot."""
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning("停止 Bot %s 失败: %s", name, e)
        self._adapters.clear()
        self._started = False

    async def mirror_to_feishu(self, msg: CompanyMessage) -> None:
        """将 CompanyMessage 通过对应角色的 Bot 发送到飞书群."""
        if not self._started:
            return

        role_name = msg.sent_from
        adapter = self._adapters.get(role_name)

        if not adapter:
            adapter = next(iter(self._adapters.values()), None)
            if not adapter:
                return

        prefix = f"[{role_name}]" if role_name else ""
        action_tag = f" ({msg.cause_by})" if msg.cause_by else ""
        header = f"{prefix}{action_tag}\n" if prefix or action_tag else ""

        content = msg.content
        if len(content) > 3000:
            content = content[:3000] + "\n\n... (内容过长已截断)"

        text = f"{header}{content}"

        try:
            from gateway.platforms.base import OutgoingMessage
            out = OutgoingMessage(
                chat_id=self._group_chat_id,
                content=text,
                message_type="text",
            )
            await adapter.send_message(out)
        except Exception as e:
            logger.warning("飞书发送失败 [%s]: %s", role_name, e)

    async def _on_feishu_message(self, platform_msg: Any) -> None:
        """飞书群消息 → CompanyMessage → publish 到 environment.

        所有 adapter 都会收到群消息，用 message_id 去重，只处理一次。
        """
        if not self._environment:
            return

        msg_id = getattr(platform_msg, "message_id", "")
        if not msg_id:
            return

        if msg_id in self._seen_msg_ids:
            return
        self._seen_msg_ids[msg_id] = True
        if len(self._seen_msg_ids) > 200:
            keys = list(self._seen_msg_ids.keys())
            for k in keys[:100]:
                del self._seen_msg_ids[k]

        from agent.company.message import CompanyMessage

        content = getattr(platform_msg, "content", "")
        sender = getattr(platform_msg, "sender", None)
        display = getattr(sender, "display_name", "") if sender else ""
        username = display or getattr(sender, "username", "Human") if sender else "Human"

        sender_id = getattr(sender, "user_id", "") if sender else ""
        for adapter in self._adapters.values():
            bot = getattr(adapter, "_bot_user", None)
            if bot and getattr(bot, "user_id", "") == sender_id:
                return

        msg = CompanyMessage(
            content=content,
            cause_by="HumanDirective",
            sent_from=username or "Human",
        )

        mention_ids = getattr(platform_msg, "mentions", [])
        if mention_ids:
            for role_name, adapter in self._adapters.items():
                bot = getattr(adapter, "_bot_user", None)
                if bot and getattr(bot, "user_id", "") in mention_ids:
                    msg.send_to = role_name
                    break

        logger.info("飞书→Company: [%s] %s (send_to=%s)",
                     msg.sent_from, content[:50], msg.send_to or "*")
        await self._environment.publish(msg)
