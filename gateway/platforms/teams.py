"""Microsoft Teams 平台适配器 — Bot Framework webhook."""

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

class TeamsAdapter(BasePlatformAdapter):
    """Microsoft Teams 适配器 — Bot Framework + webhook."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.TEAMS, config)
        self._app_id = config.get("app_id", "")
        self._app_password = config.get("app_password", "")
        self._webhook_url = config.get("webhook_url", "")

    @property
    def name(self) -> str:
        return "Microsoft Teams"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "file": True,
            "rich_text": True,
            "thread": True,
            "reaction": True,
            "edit_message": True,
            "delete_message": True,
            "typing_indicator": True,
        }

    async def start(self) -> None:
        self._running = True
        logger.info("Teams adapter started (app_id=%s)", self._app_id)

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        try:
            payload = {
                "type": "message",
                "text": message.content,
            }
            if message.reply_to:
                payload["replyToId"] = message.reply_to

            headers = {"Content-Type": "application/json"}
            if self._webhook_url:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(self._webhook_url, json=payload, headers=headers)
                    if resp.status_code in (200, 201):
                        return f"teams-{int(time.time())}"
            return None
        except Exception as e:
            logger.error("Teams send error: %s", e)
            return None

    async def handle_incoming(self, activity: dict[str, Any]) -> None:
        """处理 Bot Framework 传入的 activity."""
        if activity.get("type") != "message":
            return

        sender = activity.get("from", {})
        conversation = activity.get("conversation", {})
        chat_type = ChatType.GROUP if conversation.get("isGroup") else ChatType.PRIVATE

        msg = PlatformMessage(
            message_id=activity.get("id", ""),
            platform=PlatformType.TEAMS,
            chat=PlatformChat(
                chat_id=conversation.get("id", ""),
                chat_type=chat_type,
            ),
            sender=PlatformUser(
                user_id=sender.get("id", ""),
                username=sender.get("name", ""),
            ),
            content=activity.get("text", ""),
            raw=activity,
        )
        await self._dispatch_message(msg)
