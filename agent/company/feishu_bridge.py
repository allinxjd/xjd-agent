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
        self._start_ts: float = 0.0

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
        import asyncio
        import time as _time
        from gateway.platforms.feishu import FeishuAdapter

        self._start_ts = _time.time()

        max_retries = 3
        retry_delay = 10
        failed_roles: list[str] = []

        for role_name, cfg in self._bot_configs.items():
            started = False
            for attempt in range(1, max_retries + 1):
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
                    started = True
                    break
                except Exception as e:
                    logger.warning("飞书 Bot 启动失败: %s — %s (第%d次)", role_name, e, attempt)
                    if attempt < max_retries:
                        await asyncio.sleep(retry_delay)
            if not started:
                failed_roles.append(role_name)
                logger.error("飞书 Bot 启动彻底失败: %s (已重试%d次)", role_name, max_retries)

        if self._adapters:
            self._started = True
            for adapter in self._adapters.values():
                adapter.on_message(self._on_feishu_message)
            logger.info("飞书桥接已启动，群: %s，Bot 数: %d",
                        self._group_chat_id, len(self._adapters))
        elif failed_roles:
            self._started = True
            logger.warning("飞书桥接：所有 Bot 启动失败，watchdog 将持续重试")

        self._watchdog_task = asyncio.create_task(self._bridge_watchdog())

        if self._adapters:
            asyncio.create_task(self._startup_recovery())

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

    async def _startup_recovery(self) -> None:
        """启动后延迟拉取历史消息，弥补长连接建立期间的消息空窗."""
        import asyncio
        await asyncio.sleep(30)
        if not self._started:
            return
        adapter = next(iter(self._adapters.values()), None)
        if not adapter:
            return
        try:
            await self._recover_missed_messages(adapter, self._start_ts + 30)
            logger.info("飞书启动补漏完成")
        except Exception as e:
            logger.warning("飞书启动补漏失败: %s", e)

    async def _bridge_watchdog(self) -> None:
        """定期检查各 Bot 连接健康状态，重启掉线的 Bot，补启未启动的 Bot.

        除了检查进程存活，还检查消息活跃度 — 静默断连时强制重连并补漏消息。
        同时定期轮询 REST API 补漏（飞书群消息只推送给被@的Bot）。
        """
        import asyncio
        import time as _time
        from gateway.platforms.feishu import FeishuAdapter

        _ACTIVITY_TIMEOUT = 300  # 5 min
        _POLL_INTERVAL = 10  # 每 10 秒轮询一次
        _last_poll_ts = _time.time()
        _health_check_counter = 0

        while self._started:
            await asyncio.sleep(_POLL_INTERVAL)
            if not self._started:
                break

            # --- 定期轮询 REST API 补漏（核心：解决群消息不推送问题）---
            now = _time.time()
            adapter = next(iter(self._adapters.values()), None)
            if adapter and self._group_chat_id:
                try:
                    missed = await adapter.fetch_missed_messages(
                        self._group_chat_id, _last_poll_ts - 5
                    )
                    for event_dict in missed:
                        msg_id = event_dict.get("message", {}).get("message_id", "")
                        if msg_id in self._seen_msg_ids:
                            continue
                        self._seen_msg_ids[msg_id] = True
                        try:
                            await adapter._handle_message_event(event_dict)
                        except Exception as e:
                            logger.warning("飞书轮询消息处理失败 [%s]: %s", msg_id, e)
                    _last_poll_ts = now
                except Exception as e:
                    logger.debug("飞书轮询异常: %s", e)

            # --- 健康检查（每 60 秒一次）---
            _health_check_counter += 1
            if _health_check_counter < 6:
                continue
            _health_check_counter = 0

            for role_name, adapter in list(self._adapters.items()):
                feishu_proc = getattr(adapter, '_feishu_proc', None)
                proc_alive = feishu_proc is not None and feishu_proc.is_alive()
                last_activity = getattr(adapter, '_last_sdk_activity', 0)
                idle_secs = _time.time() - last_activity if last_activity else 0

                adapter_restart_reason = getattr(adapter, '_last_restart_reason', '')

                needs_reconnect = False
                reason = ""
                if not proc_alive:
                    needs_reconnect = True
                    reason = "process_dead"
                elif last_activity and idle_secs > _ACTIVITY_TIMEOUT:
                    needs_reconnect = True
                    reason = f"silent_timeout({idle_secs:.0f}s)"

                if adapter_restart_reason == "activity_timeout":
                    adapter._last_restart_reason = ""
                    if not needs_reconnect:
                        logger.info(
                            "飞书 Bot %s adapter 层已因静默超时重启子进程，bridge 层补漏消息",
                            role_name,
                        )
                        await self._recover_missed_messages(adapter, _time.time())
                        continue

                if not needs_reconnect:
                    continue

                logger.warning(
                    "飞书 Bot %s 需要重连 (reason=%s, proc_alive=%s, idle=%.0fs)",
                    role_name, reason, proc_alive, idle_secs,
                )
                reconnect_ts = _time.time()
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

                    await self._recover_missed_messages(new_adapter, reconnect_ts)
                except Exception as e:
                    logger.error("飞书 Bot %s 重连失败: %s", role_name, e)

            missing = set(self._bot_configs.keys()) - set(self._adapters.keys())
            for role_name in missing:
                cfg = self._bot_configs[role_name]
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
                    logger.info("飞书 Bot %s 补启成功", role_name)
                except Exception as e:
                    logger.warning("飞书 Bot %s 补启失败: %s", role_name, e)

    async def _recover_missed_messages(self, adapter: Any, reconnect_ts: float) -> None:
        """重连后通过 REST API 补漏断连期间的消息."""
        if not self._group_chat_id or not self._environment:
            return
        try:
            since_ts = reconnect_ts - 600
            missed = await adapter.fetch_missed_messages(self._group_chat_id, since_ts)
            if not missed:
                logger.info("飞书补漏: 无遗漏消息")
                return
            for event_dict in missed:
                msg_id = event_dict.get("message", {}).get("message_id", "")
                if msg_id in self._seen_msg_ids:
                    continue
                self._seen_msg_ids[msg_id] = True
                try:
                    await adapter._handle_message_event(event_dict)
                except Exception as e:
                    logger.warning("飞书补漏消息处理失败 [%s]: %s", msg_id, e)
            logger.info("飞书补漏: 注入 %d 条遗漏消息", len(missed))
        except Exception as e:
            logger.warning("飞书消息补漏异常: %s", e)

    _MSG_LIMIT = 3500

    @staticmethod
    def _split_message(text: str, limit: int = 3500) -> list[str]:
        """Split long text into chunks that fit Feishu's message limit.

        Splits on paragraph boundaries first, then sentence boundaries."""
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            cut = remaining.rfind("\n\n", 0, limit)
            if cut < limit // 3:
                cut = remaining.rfind("\n", 0, limit)
            if cut < limit // 3:
                for sep in ("。", "；", ".", ";", "，", ",", " "):
                    cut = remaining.rfind(sep, 0, limit)
                    if cut >= limit // 3:
                        cut += len(sep)
                        break
            if cut < limit // 3:
                cut = limit
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        return chunks

    _DOC_ACTIONS = {"WritePRD", "WriteDesign", "CodeReview", "WriteTest", "DeployPlan", "StageFile"}
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
            logger.warning("mirror_to_feishu: bridge 未启动，丢弃消息 [%s] %s", msg.sent_from, msg.cause_by)
            return

        if msg.cause_by == "HumanDirective":
            return

        role_name = msg.sent_from
        adapter = self._adapters.get(role_name)

        if not adapter:
            adapter = next(iter(self._adapters.values()), None)
            if not adapter:
                logger.warning("mirror_to_feishu: 无可用 adapter，丢弃消息 [%s] %s", role_name, msg.cause_by)
                return

        content = msg.content
        content = self._clean_llm_artifacts(content)

        # 原型/UI 截图：发送为图片
        if msg.cause_by == "StageImage" and msg.metadata.get("image_path"):
            from pathlib import Path as _P
            img_path = _P(msg.metadata["image_path"])
            if img_path.exists():
                try:
                    from gateway.platforms.base import OutgoingMessage, MessageType
                    img_data = img_path.read_bytes()
                    image_key = await adapter._upload_image(img_data)
                    out = OutgoingMessage(
                        chat_id=self._group_chat_id,
                        message_type=MessageType.IMAGE,
                        media_url=image_key,
                    )
                    await adapter.send_message(out)
                    return
                except Exception as e:
                    logger.warning("飞书图片发送失败 [%s]，降级为文件: %r", role_name, e)

        force_file = msg.cause_by == "StageFile"
        if (force_file or len(content) > self._FILE_THRESHOLD) and msg.cause_by != "ChatReply" and msg.cause_by != "RoleCheckin":
            filename = msg.metadata.get("filename") or self._action_to_filename(msg.cause_by)
            file_path = msg.metadata.get("file_path")
            if file_path:
                from pathlib import Path as _P
                _fp = _P(file_path)
                if _fp.exists():
                    file_data = _fp.read_bytes()
                else:
                    file_data = content.encode("utf-8")
            else:
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

        content = self._strip_markdown(content)
        chunks = self._split_message(content)

        try:
            from gateway.platforms.base import OutgoingMessage, MessageType
            import asyncio
            import re
            for i, chunk in enumerate(chunks):
                if i > 0:
                    await asyncio.sleep(0.5)
                url_match = re.search(r'https?://[\d.]+:\d+\S*', chunk)
                if url_match:
                    url = url_match.group(0)
                    before = chunk[:url_match.start()]
                    after = chunk[url_match.end():]
                    post_elements = []
                    if before.strip():
                        post_elements.append({"tag": "text", "text": before})
                    post_elements.append({"tag": "a", "text": url, "href": url})
                    if after.strip():
                        post_elements.append({"tag": "text", "text": after})
                    out = OutgoingMessage(
                        chat_id=self._group_chat_id,
                        message_type=MessageType.RICH_TEXT,
                        metadata={"post_content": [post_elements]},
                    )
                else:
                    out = OutgoingMessage(
                        chat_id=self._group_chat_id,
                        content=chunk,
                        message_type="text",
                    )
                await adapter.send_message(out)
        except Exception as e:
            logger.warning("飞书发送失败 [%s]: %r", role_name, e)

    async def _on_feishu_message(self, platform_msg: Any) -> None:
        """飞书群消息 → CompanyMessage → publish 到 environment.

        所有 adapter 都会收到群消息，用 message_id 去重，只处理一次。
        忽略 bridge 启动之前的历史消息（防止重启后重复投递）。
        """
        if not self._environment:
            return

        msg_id = getattr(platform_msg, "message_id", "")
        if not msg_id:
            return

        msg_ts = getattr(platform_msg, "timestamp", 0)
        if msg_ts and self._start_ts and msg_ts < self._start_ts:
            logger.debug("忽略历史消息 %s (msg_ts=%.0f < start_ts=%.0f)", msg_id, msg_ts, self._start_ts)
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
