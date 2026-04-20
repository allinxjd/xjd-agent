"""Twitter/X 平台适配器 — DM API."""

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

class TwitterAdapter(BasePlatformAdapter):
    """Twitter/X 适配器 — DM API + 轮询."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.TWITTER, config)
        self._bearer_token = config.get("bearer_token", "")
        self._api_key = config.get("api_key", "")
        self._api_secret = config.get("api_secret", "")
        self._access_token = config.get("access_token", "")
        self._access_secret = config.get("access_token_secret", "")
        self._poll_interval = config.get("poll_interval", 15)
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Twitter/X"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "file": False,
            "voice": False,
            "rich_text": False,
            "thread": False,
            "reaction": False,
        }

    async def start(self) -> None:
        if not self._bearer_token:
            raise ValueError("Twitter adapter 需要 bearer_token")
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Twitter adapter started")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        """轮询 DM 收件箱."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx 未安装")
            return

        while self._running:
            try:
                headers = {"Authorization": f"Bearer {self._bearer_token}"}
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(
                        "https://api.twitter.com/2/dm_events",
                        headers=headers,
                        params={"event_types": "MessageCreate"},
                    )
                    if resp.status_code == 200:
                        for event in resp.json().get("data", []):
                            sender_id = event.get("sender_id", "")
                            msg = PlatformMessage(
                                message_id=event.get("id", ""),
                                platform=PlatformType.TWITTER,
                                chat=PlatformChat(
                                    chat_id=event.get("dm_conversation_id", sender_id),
                                    chat_type=ChatType.PRIVATE,
                                ),
                                sender=PlatformUser(user_id=sender_id),
                                content=event.get("text", ""),
                                raw=event,
                            )
                            await self._dispatch_message(msg)
            except Exception as e:
                logger.error("Twitter poll error: %s", e)

            await asyncio.sleep(self._poll_interval)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        try:
            headers = {"Authorization": f"Bearer {self._bearer_token}"}
            payload = {
                "text": message.content,
                "conversation_id": message.chat_id,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.twitter.com/2/dm_conversations/messages",
                    json=payload, headers=headers,
                )
                if resp.status_code in (200, 201):
                    return resp.json().get("data", {}).get("id", f"tw-{int(time.time())}")
                logger.error("Twitter send failed: %s", resp.text)
                return None
        except Exception as e:
            logger.error("Twitter send error: %s", e)
            return None
