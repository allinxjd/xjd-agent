"""SMS 平台适配器 — Twilio REST API."""

from __future__ import annotations

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

class SMSAdapter(BasePlatformAdapter):
    """SMS 适配器 — Twilio."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.SMS, config)
        self._account_sid = config.get("account_sid", "")
        self._auth_token = config.get("auth_token", "")
        self._from_number = config.get("from_number", "")

    @property
    def name(self) -> str:
        return "SMS"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,  # MMS
            "file": False,
            "voice": False,
            "rich_text": False,
            "thread": False,
            "reaction": False,
        }

    async def start(self) -> None:
        if not self._account_sid or not self._auth_token:
            raise ValueError("SMS adapter 需要 account_sid 和 auth_token")
        self._running = True
        logger.info("SMS adapter started (from=%s)", self._from_number)

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import httpx
        except ImportError:
            return None

        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{self._account_sid}/Messages.json"
            data = {
                "To": message.chat_id,
                "From": self._from_number,
                "Body": message.content,
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url, data=data,
                    auth=(self._account_sid, self._auth_token),
                )
                if resp.status_code == 201:
                    return resp.json().get("sid", f"sms-{int(time.time())}")
                logger.error("SMS send failed: %s", resp.text)
                return None
        except Exception as e:
            logger.error("SMS send error: %s", e)
            return None

    async def handle_webhook(self, form_data: dict[str, str]) -> None:
        """处理 Twilio webhook 传入的短信."""
        msg = PlatformMessage(
            message_id=form_data.get("MessageSid", ""),
            platform=PlatformType.SMS,
            chat=PlatformChat(
                chat_id=form_data.get("From", ""),
                chat_type=ChatType.PRIVATE,
            ),
            sender=PlatformUser(
                user_id=form_data.get("From", ""),
                username=form_data.get("From", ""),
            ),
            content=form_data.get("Body", ""),
            raw=form_data,
        )
        await self._dispatch_message(msg)
