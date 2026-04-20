"""Signal 平台适配器 — signal-cli-rest-api.

通过 HTTP 调用 signal-cli REST API 收发消息。
需要部署 signal-cli-rest-api: https://github.com/bbernhard/signal-cli-rest-api
"""

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

class SignalAdapter(BasePlatformAdapter):
    """Signal 适配器 — HTTP 调用 signal-cli-rest-api."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.SIGNAL, config)
        self._api_url = config.get("api_url", "http://localhost:8080").rstrip("/")
        self._phone = config.get("phone_number", "")
        self._poll_interval = config.get("poll_interval", 2)
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Signal"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "file": True,
            "voice": False,
            "video": False,
            "edit_message": False,
            "delete_message": False,
            "reply": True,
            "reaction": True,
        }

    async def start(self) -> None:
        if not self._phone:
            raise ValueError("Signal adapter 需要 phone_number 配置")
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Signal adapter started (phone=%s)", self._phone)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        """轮询 signal-cli-rest-api 接收消息."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx 未安装")
            return

        while self._running:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(f"{self._api_url}/v1/receive/{self._phone}")
                    if resp.status_code == 200:
                        for item in resp.json():
                            envelope = item.get("envelope", {})
                            data_msg = envelope.get("dataMessage")
                            if not data_msg:
                                continue

                            sender = envelope.get("source", "")
                            text = data_msg.get("message", "")
                            ts = data_msg.get("timestamp", 0)
                            group = data_msg.get("groupInfo", {})

                            chat_id = group.get("groupId", sender)
                            chat_type = ChatType.GROUP if group else ChatType.PRIVATE

                            msg = PlatformMessage(
                                message_id=str(ts),
                                platform=PlatformType.SIGNAL,
                                chat=PlatformChat(
                                    chat_id=chat_id,
                                    chat_type=chat_type,
                                ),
                                sender=PlatformUser(user_id=sender, username=sender),
                                message_type=MessageType.TEXT,
                                content=text,
                                timestamp=ts / 1000.0,
                                raw=item,
                            )
                            await self._dispatch_message(msg)
            except Exception as e:
                logger.error("Signal poll error: %s", e)

            await asyncio.sleep(self._poll_interval)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        try:
            payload: dict[str, Any] = {
                "message": message.content,
                "number": self._phone,
                "recipients": [message.chat_id],
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._api_url}/v2/send",
                    json=payload,
                )
                if resp.status_code in (200, 201):
                    return f"sent-{int(time.time())}"
                logger.error("Signal send failed: %s", resp.text)
                return None
        except Exception as e:
            logger.error("Signal send error: %s", e)
            return None

    async def health_check(self) -> dict[str, Any]:
        return {
            "platform": "signal",
            "name": self.name,
            "running": self._running,
            "phone": self._phone,
        }
