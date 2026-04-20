"""Reddit 平台适配器 — asyncpraw."""

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

class RedditAdapter(BasePlatformAdapter):
    """Reddit 适配器 — asyncpraw 轮询收件箱."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.REDDIT, config)
        self._client_id = config.get("client_id", "")
        self._client_secret = config.get("client_secret", "")
        self._username = config.get("username", "")
        self._password = config.get("password", "")
        self._user_agent = config.get("user_agent", "xjd-agent:v0.1")
        self._poll_interval = config.get("poll_interval", 30)
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Reddit"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": False,
            "file": False,
            "voice": False,
            "rich_text": True,  # Markdown
            "thread": True,
            "reaction": False,
        }

    async def start(self) -> None:
        if not self._client_id:
            raise ValueError("Reddit adapter 需要 client_id")
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Reddit adapter started (user=%s)", self._username)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        """轮询 Reddit 收件箱."""
        try:
            import asyncpraw
        except ImportError:
            logger.error("asyncpraw 未安装, pip install 'xjd-agent[reddit]'")
            return

        reddit = asyncpraw.Reddit(
            client_id=self._client_id,
            client_secret=self._client_secret,
            username=self._username,
            password=self._password,
            user_agent=self._user_agent,
        )

        while self._running:
            try:
                inbox = reddit.inbox
                async for item in inbox.unread(limit=10):
                    author_name = str(item.author) if item.author else "unknown"
                    msg = PlatformMessage(
                        message_id=item.id,
                        platform=PlatformType.REDDIT,
                        chat=PlatformChat(
                            chat_id=item.id,
                            chat_type=ChatType.PRIVATE,
                        ),
                        sender=PlatformUser(
                            user_id=author_name,
                            username=author_name,
                        ),
                        content=item.body or "",
                        raw={"id": item.id, "subject": getattr(item, "subject", "")},
                    )
                    await self._dispatch_message(msg)
                    await item.mark_read()
            except Exception as e:
                logger.error("Reddit poll error: %s", e)

            await asyncio.sleep(self._poll_interval)

        await reddit.close()

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import asyncpraw
        except ImportError:
            return None

        try:
            reddit = asyncpraw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                username=self._username,
                password=self._password,
                user_agent=self._user_agent,
            )
            redditor = await reddit.redditor(message.chat_id)
            await redditor.message(
                subject="XJD Agent",
                message=message.content,
            )
            await reddit.close()
            return f"reddit-{int(time.time())}"
        except Exception as e:
            logger.error("Reddit send error: %s", e)
            return None
