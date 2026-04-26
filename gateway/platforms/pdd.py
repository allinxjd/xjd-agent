"""拼多多客服 Gateway 适配器.

将 PddCSClient (WebSocket 实时客服) 包装为 BasePlatformAdapter，
使 PDD 客服消息能通过 Gateway 统一路由到 AgentEngine。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from gateway.platforms.base import (
    BasePlatformAdapter, ChatType, MessageType, OutgoingMessage,
    PlatformChat, PlatformMessage, PlatformType, PlatformUser,
)

logger = logging.getLogger(__name__)


class PDDAdapter(BasePlatformAdapter):
    """拼多多客服平台适配器."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.PDD, config)
        self._shop_id = config.get("shop_id", "")
        self._cs_client: Any = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._bot_user = PlatformUser(
            user_id=f"pdd_cs_{self._shop_id}",
            username="PDD CS Bot",
            display_name="拼多多客服",
        )

    @property
    def name(self) -> str:
        return "拼多多客服"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "voice": False,
            "video": False,
            "file": False,
            "rich_text": False,
            "interactive": False,
            "edit_message": False,
            "delete_message": False,
            "reaction": False,
            "thread": False,
            "typing_indicator": False,
        }

    async def start(self) -> None:
        from agent.ecommerce.platforms.pdd_cs import PddCSClient
        from agent.ecommerce.session import get_session_manager

        sm = get_session_manager()
        session = await sm.get_session("pdd")
        cookies = {}
        if hasattr(session, "context") and session.context:
            raw_cookies = await session.context.cookies()
            cookies = {c["name"]: c["value"] for c in raw_cookies}

        self._cs_client = PddCSClient(cookies, shop_id=self._shop_id)
        ok = await self._cs_client.connect()
        if not ok:
            raise ConnectionError("PDD WebSocket 连接失败")

        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_messages())
        logger.info("PDD adapter started (shop=%s)", self._shop_id)

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task and not self._consumer_task.done():
            self._consumer_task.cancel()
        if self._cs_client:
            await self._cs_client.disconnect()
        logger.info("PDD adapter stopped")

    async def _consume_messages(self) -> None:
        while self._running and self._cs_client:
            try:
                msg = await asyncio.wait_for(self._cs_client.queue.get(), timeout=1.0)
                platform_msg = PlatformMessage(
                    message_id=msg.msg_id or str(int(time.time() * 1000)),
                    platform=PlatformType.PDD,
                    chat=PlatformChat(
                        chat_id=msg.from_uid,
                        chat_type=ChatType.PRIVATE,
                        title="",
                    ),
                    sender=PlatformUser(
                        user_id=msg.from_uid,
                        username=msg.from_uid,
                        display_name="",
                    ),
                    message_type=MessageType.TEXT if msg.msg_type == 0 else MessageType.IMAGE,
                    content=msg.content,
                    timestamp=msg.timestamp,
                    raw=msg.raw,
                )
                await self._dispatch_message(platform_msg)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("PDD consume error: %s", e)

    async def send_message(self, message: OutgoingMessage) -> str:
        if not self._cs_client:
            return ""
        if message.message_type == MessageType.IMAGE and message.media_url:
            ok = await self._cs_client.send_image(message.chat_id, message.media_url)
        else:
            ok = await self._cs_client.send_text(message.chat_id, message.content)
        if ok:
            return f"pdd_{int(time.time() * 1000)}"
        return ""

    async def send_text(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> str:
        if self._cs_client:
            ok = await self._cs_client.send_text(chat_id, text)
            if ok:
                return f"pdd_{int(time.time() * 1000)}"
        return ""

    async def send_image(
        self,
        chat_id: str,
        image_url: Optional[str] = None,
        image_data: Optional[bytes] = None,
        caption: str = "",
    ) -> str:
        if self._cs_client and image_url:
            ok = await self._cs_client.send_image(chat_id, image_url)
            if ok:
                return f"pdd_img_{int(time.time() * 1000)}"
        return ""
