"""Facebook Messenger 平台适配器 — Graph API."""

from __future__ import annotations

import asyncio
import logging
import time
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

class FacebookAdapter(BasePlatformAdapter):
    """Facebook Messenger 适配器 — Graph API + webhook."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.FACEBOOK, config)
        self._page_token = config.get("page_access_token", "")
        self._verify_token = config.get("verify_token", "")
        self._api_version = config.get("api_version", "v19.0")

    @property
    def name(self) -> str:
        return "Facebook Messenger"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "file": True,
            "voice": True,
            "rich_text": True,
            "interactive": True,
            "typing_indicator": True,
            "reaction": False,
            "thread": False,
        }

    async def start(self) -> None:
        if not self._page_token:
            raise ValueError("Facebook adapter 需要 page_access_token")
        self._running = True
        logger.info("Facebook Messenger adapter started")

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        try:
            url = f"https://graph.facebook.com/{self._api_version}/me/messages"
            payload = {
                "recipient": {"id": message.chat_id},
                "message": {"text": message.content},
            }
            params = {"access_token": self._page_token}
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload, params=params)
                if resp.status_code == 200:
                    return resp.json().get("message_id", f"fb-{int(time.time())}")
                logger.error("Facebook send failed: %s", resp.text)
                return None
        except Exception as e:
            logger.error("Facebook send error: %s", e)
            return None

    async def handle_webhook(self, body: dict[str, Any]) -> None:
        """处理 Facebook webhook 事件."""
        for entry in body.get("entry", []):
            for event in entry.get("messaging", []):
                message_data = event.get("message")
                if not message_data:
                    continue

                sender_id = event.get("sender", {}).get("id", "")
                msg = PlatformMessage(
                    message_id=message_data.get("mid", ""),
                    platform=PlatformType.FACEBOOK,
                    chat=PlatformChat(
                        chat_id=sender_id,
                        chat_type=ChatType.PRIVATE,
                    ),
                    sender=PlatformUser(user_id=sender_id, username=sender_id),
                    content=message_data.get("text", ""),
                    raw=event,
                )
                await self._dispatch_message(msg)
