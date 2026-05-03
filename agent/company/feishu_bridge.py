"""FeishuBridge — 飞书双向桥接，每个角色绑定独立飞书 Bot."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from agent.company.chat_bridge import ChatBridge

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


class FeishuBridge(ChatBridge):
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

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Remove markdown formatting that Feishu text API doesn't support."""
        import re
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        return text

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
                bot = getattr(adapter, "_bot_user", None)
                bot_id = getattr(bot, "user_id", "") if bot else ""
                bot_name = getattr(bot, "display_name", "") if bot else ""
                logger.info("飞书 Bot 启动: %s (app=%s, open_id=%s, name=%s)",
                            role_name, cfg.app_id, bot_id, bot_name)
            except Exception as e:
                logger.error("飞书 Bot 启动失败: %s — %s", role_name, e)

        if self._adapters:
            self._started = True
            for adapter in self._adapters.values():
                adapter.on_message(self._on_feishu_message)
            logger.info("飞书桥接已启动，群: %s，Bot 数: %d",
                        self._group_chat_id, len(self._adapters))
            import asyncio
            self._watchdog_task = asyncio.create_task(self._bridge_watchdog())

    async def stop(self) -> None:
        """停止所有飞书 Bot."""
        self._started = False
        if hasattr(self, '_watchdog_task') and self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        for name, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception as e:
                logger.warning("停止 Bot %s 失败: %s", name, e)
        self._adapters.clear()

    async def _bridge_watchdog(self) -> None:
        """定期检查各 Bot 连接健康状态，重启掉线的 Bot."""
        import asyncio
        import time as _time
        from gateway.platforms.feishu import FeishuAdapter

        while self._started:
            await asyncio.sleep(120)
            if not self._started:
                break
            for role_name, adapter in list(self._adapters.items()):
                ws_thread = getattr(adapter, '_ws_thread', None)
                last_activity = getattr(adapter, '_last_sdk_activity', 0)
                idle_seconds = _time.time() - last_activity if last_activity else 999
                thread_alive = ws_thread.is_alive() if ws_thread else False
                if thread_alive and idle_seconds < 300:
                    continue
                logger.warning(
                    "飞书 Bot %s 疑似掉线 (thread_alive=%s, idle=%.0fs)，重建连接",
                    role_name, thread_alive, idle_seconds,
                )
                cfg = self._bot_configs.get(role_name)
                if not cfg:
                    continue
                try:
                    await adapter.stop()
                except Exception:
                    pass
                try:
                    new_adapter = FeishuAdapter({
                        "app_id": cfg.app_id,
                        "app_secret": cfg.app_secret,
                        "verification_token": cfg.verification_token,
                        "encrypt_key": cfg.encrypt_key,
                        "mode": "long_poll",
                        "accept_group_no_mention": True,
                    })
                    await new_adapter.start()
                    new_adapter.on_message(self._on_feishu_message)
                    self._adapters[role_name] = new_adapter
                    logger.info("飞书 Bot %s 重连成功", role_name)
                except Exception as e:
                    logger.error("飞书 Bot %s 重连失败: %s", role_name, e)

    _DOC_ACTIONS = {"WritePRD", "WriteDesign", "CodeReview", "WriteTest", "DeployPlan"}
    _FILE_THRESHOLD = 500

    @staticmethod
    def _action_to_filename(action_name: str, locale: Any = None) -> str:
        if locale:
            val = locale.get(f"filenames.{action_name}")
            if val:
                return val
        mapping = {
            "WritePRD": "PRD-需求文档.md",
            "WriteDesign": "技术设计方案.md",
            "CodeReview": "代码审查报告.md",
            "WriteTest": "测试报告.md",
            "DeployPlan": "部署方案.md",
            "QuickTask": "执行报告.md",
        }
        return mapping.get(action_name, f"{action_name}.md")

    @staticmethod
    def _clean_llm_artifacts(text: str) -> str:
        """Remove LLM-specific markup that leaks into output."""
        import re
        text = re.sub(r'<\uff5c\uff5cDSML\uff5c\uff5c[^>]*>.*?(?:</\uff5c\uff5cDSML\uff5c\uff5c[^>]*>|$)', '', text, flags=re.DOTALL)
        text = re.sub(r'</?\uff5c\uff5c[^>]*>', '', text)
        text = re.sub(r'</?antml:[a-z_]+[^>]*>', '', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    async def mirror_to_chat(self, msg: CompanyMessage) -> None:
        """ChatBridge 接口实现."""
        await self.mirror_to_feishu(msg)

    async def mirror_to_feishu(self, msg: CompanyMessage) -> None:
        """将 CompanyMessage 通过对应角色的 Bot 发送到飞书群."""
        if not self._started:
            return

        if msg.cause_by == "HumanDirective":
            return

        role_name = msg.sent_from
        adapter = self._adapters.get(role_name)

        if not adapter:
            adapter = next(iter(self._adapters.values()), None)
            if not adapter:
                return

        content = msg.content
        content = self._clean_llm_artifacts(content)

        if len(content) > self._FILE_THRESHOLD and msg.cause_by != "ChatReply" and msg.cause_by != "RoleCheckin":
            filename = self._action_to_filename(msg.cause_by)
            file_data = content.encode("utf-8")
            try:
                from gateway.platforms.base import OutgoingMessage, MessageType
                out = OutgoingMessage(
                    chat_id=self._group_chat_id,
                    message_type=MessageType.FILE,
                    media_data=file_data,
                    metadata={"filename": filename},
                )
                await adapter.send_message(out)
                summary = content[:200].replace("\n", " ").strip() + "..."
                summary = self._strip_markdown(summary)
                text_out = OutgoingMessage(
                    chat_id=self._group_chat_id,
                    content=f"{filename} 已发送，摘要：{summary}",
                    message_type="text",
                )
                await adapter.send_message(text_out)
                return
            except Exception as e:
                logger.warning("飞书文件发送失败 [%s]，降级为文本: %r", role_name, e)

        if len(content) > 3000:
            content = content[:3000] + "\n\n... (内容过长已截断)"

        content = self._strip_markdown(content)

        try:
            from gateway.platforms.base import OutgoingMessage
            out = OutgoingMessage(
                chat_id=self._group_chat_id,
                content=content,
                message_type="text",
            )
            await adapter.send_message(out)
        except Exception as e:
            logger.warning("飞书发送失败 [%s]: %r", role_name, e)

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

        metadata = getattr(platform_msg, "metadata", {}) or {}
        mention_details = metadata.get("mention_details", [])
        mention_ids = getattr(platform_msg, "mentions", [])
        mention_ids = [mid for mid in mention_ids if mid]

        # Strategy 1: match mention open_id against bot open_id
        if mention_ids:
            for role_name, adapter in self._adapters.items():
                bot = getattr(adapter, "_bot_user", None)
                if not bot:
                    continue
                bot_open_id = getattr(bot, "user_id", "")
                if bot_open_id and bot_open_id in mention_ids:
                    msg.send_to = role_name
                    logger.info("飞书@匹配(open_id): %s → %s", bot_open_id, role_name)
                    break

        # Strategy 2: match mention display name against bot display name
        if not msg.send_to and mention_details:
            for detail in mention_details:
                mention_name = detail.get("name", "")
                if not mention_name:
                    continue
                for role_name, adapter in self._adapters.items():
                    bot = getattr(adapter, "_bot_user", None)
                    if not bot:
                        continue
                    bot_name = getattr(bot, "display_name", "") or getattr(bot, "username", "")
                    if bot_name and bot_name == mention_name:
                        msg.send_to = role_name
                        logger.info("飞书@匹配(name): %s → %s", mention_name, role_name)
                        break
                if msg.send_to:
                    break

        # Strategy 3: match mention name against role keyword map
        if not msg.send_to and mention_details:
            role_nick_map = {
                "PM": ["PM", "产品", "产品经理"],
                "Developer": ["开发", "程序员"],
                "Reviewer": ["审查", "审查员"],
                "QA": ["测试", "QA"],
                "DevOps": ["运维", "部署"],
            }
            for detail in mention_details:
                mention_name = detail.get("name", "")
                if not mention_name:
                    continue
                for role_name, nicks in role_nick_map.items():
                    if role_name in self._adapters and mention_name in nicks:
                        msg.send_to = role_name
                        logger.info("飞书@匹配(nick): %s → %s", mention_name, role_name)
                        break
                if msg.send_to:
                    break

        if mention_details:
            logger.debug("飞书 mention_details: %s, mention_ids: %s", mention_details, mention_ids)

        logger.info("飞书→Company: [%s] %s (send_to=%s)",
                     msg.sent_from, content[:50], msg.send_to or "*")
        await self._environment.publish(msg)
