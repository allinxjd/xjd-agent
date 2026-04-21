"""微信 ClawBot (iLink 协议) 适配器.

通过腾讯官方 iLink Bot API 接入个人微信，使用 wechat-clawbot SDK。
消息收取: long-poll (get_updates)
消息发送: HTTP POST (send_message)

依赖: pip install "xjd-agent[wechat-clawbot]"
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    ChatType,
    MessageType,
    OutgoingMessage,
    PlatformChat,
    PlatformMessage,
    PlatformType,
    PlatformUser,
)

logger = logging.getLogger(__name__)

ILINK_BASE_URL = "https://ilinkai.weixin.qq.com"


class WeChatClawBotAdapter(BasePlatformAdapter):
    """微信 ClawBot 适配器 — 个人微信 iLink 协议."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.WECHAT_CLAWBOT, config)
        self._bot_token = config.get("bot_token", "")
        self._base_url = config.get("base_url", ILINK_BASE_URL).rstrip("/")
        self._poll_task: Optional[asyncio.Task] = None
        self._context_tokens: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "微信 ClawBot"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": True, "voice": True, "video": False,
            "file": True, "rich_text": False, "interactive": False,
            "edit_message": False, "delete_message": False,
            "reaction": False, "thread": False, "typing_indicator": True,
        }

    async def start(self) -> None:
        if not self._bot_token:
            raise ValueError("微信 ClawBot bot_token 未配置")
        try:
            from wechat_clawbot.api.client import WeixinApiOptions
        except ImportError:
            raise ImportError(
                'wechat-clawbot 未安装。请运行: pip install "xjd-agent[wechat-clawbot]"'
            )
        self._opts = WeixinApiOptions(base_url=self._base_url, token=self._bot_token)
        self._bot_user = PlatformUser(
            user_id="clawbot", username="ClawBot",
            display_name="XJD ClawBot", is_bot=True,
        )
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("微信 ClawBot 适配器已启动 (iLink long-poll)")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        self._poll_task = None
        logger.info("微信 ClawBot 适配器已停止")

    # ── Long-poll 循环 ──

    async def _poll_loop(self) -> None:
        from wechat_clawbot.api.client import get_updates
        retry_delay = 1.0
        while self._running:
            try:
                resp = await get_updates(
                    base_url=self._opts.base_url, token=self._opts.token,
                )
                retry_delay = 1.0
                for msg in resp.msgs or []:
                    await self._handle_incoming(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("ClawBot poll 失败: %s, %0.fs 后重试", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    async def _handle_incoming(self, msg: Any) -> None:
        try:
            from wechat_clawbot.api.types import MessageItemType

            sender_id = getattr(msg, "from_user_id", "") or ""
            context_token = getattr(msg, "context_token", "") or ""
            if sender_id and context_token:
                self._context_tokens[sender_id] = context_token

            content = ""
            msg_type = MessageType.TEXT
            media_url = ""
            items = getattr(msg, "item_list", None) or []
            for item in items:
                item_type = getattr(item, "type", None)
                if item_type == MessageItemType.TEXT:
                    text_item = getattr(item, "text_item", None)
                    if text_item:
                        content += getattr(text_item, "text", "") or ""
                elif item_type == MessageItemType.IMAGE:
                    msg_type = MessageType.IMAGE
                    img = getattr(item, "image_item", None)
                    if img:
                        media_url = getattr(img, "full_url", "") or getattr(img, "url", "") or ""
                elif item_type == MessageItemType.FILE:
                    msg_type = MessageType.FILE
                    f = getattr(item, "file_item", None)
                    if f:
                        media_url = getattr(f, "full_url", "") or getattr(f, "url", "") or ""
                elif item_type == MessageItemType.VOICE:
                    msg_type = MessageType.VOICE
                    v = getattr(item, "voice_item", None)
                    if v:
                        media_url = getattr(v, "full_url", "") or getattr(v, "url", "") or ""

            if not content and msg_type == MessageType.TEXT:
                return

            sender = PlatformUser(user_id=sender_id, username=sender_id)
            chat = PlatformChat(
                chat_id=sender_id, chat_type=ChatType.PRIVATE,
                platform=PlatformType.WECHAT_CLAWBOT,
            )
            platform_msg = PlatformMessage(
                message_id=getattr(msg, "msg_id", "") or uuid.uuid4().hex[:16],
                platform=PlatformType.WECHAT_CLAWBOT,
                chat=chat, sender=sender,
                message_type=msg_type, content=content.strip(),
                media_url=media_url, timestamp=time.time(), raw=msg,
            )
            await self._dispatch_message(platform_msg)
        except Exception as e:
            logger.error("ClawBot 消息处理失败: %s", e, exc_info=True)

    # ── 发送消息 ──

    async def send_message(self, message: OutgoingMessage) -> str:
        from wechat_clawbot.api.client import send_message as sdk_send
        from wechat_clawbot.api.types import (
            MessageItemType as MIT, MessageState, MessageType as MT,
            SendMessageReq, WeixinMessage, MessageItem, TextItem,
        )

        to_user = message.chat_id
        ctx_token = self._context_tokens.get(to_user, "")
        items: list[MessageItem] = []

        if message.message_type == MessageType.TEXT or message.content:
            items.append(MessageItem(type=MIT.TEXT, text_item=TextItem(text=message.content)))

        try:
            await sdk_send(self._opts, SendMessageReq(msg=WeixinMessage(
                to_user_id=to_user,
                client_id=uuid.uuid4().hex[:16],
                message_type=MT.BOT,
                message_state=MessageState.FINISH,
                item_list=items,
                context_token=ctx_token,
            )))
            return uuid.uuid4().hex[:16]
        except Exception as e:
            logger.error("ClawBot 发送消息失败: %s", e)
            return ""
