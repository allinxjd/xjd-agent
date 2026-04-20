"""Google Chat 平台适配器.

支持两种模式:
1. Webhook — 简单推送消息
2. Google Chat API — 完整双向通信 (需要 Service Account)
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

class GoogleChatAdapter(BasePlatformAdapter):
    """Google Chat 适配器."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.GOOGLE_CHAT, config)
        self._webhook_url = config.get("webhook_url", "")
        self._credentials_file = config.get("credentials_file", "")
        self._space_id = config.get("space_id", "")
        self._poll_interval = config.get("poll_interval", 5)
        self._poll_task: Optional[asyncio.Task] = None
        self._service = None

    @property
    def name(self) -> str:
        return "Google Chat"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": False, "file": False,
            "voice": False, "video": False,
            "rich_text": True,
            "edit_message": True, "delete_message": True,
            "thread": True,
        }

    async def start(self) -> None:
        if self._credentials_file:
            try:
                from google.oauth2 import service_account
                from googleapiclient.discovery import build

                creds = service_account.Credentials.from_service_account_file(
                    self._credentials_file,
                    scopes=["https://www.googleapis.com/auth/chat.bot"],
                )
                self._service = build("chat", "v1", credentials=creds)
                self._running = True
                self._poll_task = asyncio.create_task(self._poll_loop())
                logger.info("Google Chat adapter started (API mode)")
            except ImportError:
                raise ImportError(
                    "google-api-python-client 未安装。请运行:\n"
                    "  pip install google-auth google-api-python-client"
                )
        elif self._webhook_url:
            self._running = True
            logger.info("Google Chat adapter started (webhook mode, send-only)")
        else:
            raise ValueError("Google Chat 需要 webhook_url 或 credentials_file")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()

    async def _poll_loop(self) -> None:
        """轮询 Google Chat API 获取消息."""
        while self._running and self._service:
            try:
                if self._space_id:
                    result = await asyncio.to_thread(
                        self._service.spaces().messages().list(
                            parent=f"spaces/{self._space_id}",
                            pageSize=10,
                        ).execute
                    )
                    for msg_data in result.get("messages", []):
                        sender = msg_data.get("sender", {})
                        msg = PlatformMessage(
                            message_id=msg_data.get("name", ""),
                            platform=PlatformType.GOOGLE_CHAT,
                            chat=PlatformChat(
                                chat_id=self._space_id,
                                chat_type=ChatType.GROUP,
                            ),
                            sender=PlatformUser(
                                user_id=sender.get("name", ""),
                                display_name=sender.get("displayName", ""),
                            ),
                            message_type=MessageType.TEXT,
                            content=msg_data.get("text", ""),
                            raw=msg_data,
                        )
                        await self._dispatch_message(msg)
            except Exception as e:
                logger.error("Google Chat poll error: %s", e)

            await asyncio.sleep(self._poll_interval)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        # Webhook 模式
        if self._webhook_url:
            try:
                import httpx
                payload = {"text": message.content}
                if message.metadata.get("thread_key"):
                    payload["thread"] = {"threadKey": message.metadata["thread_key"]}
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.post(self._webhook_url, json=payload)
                    if resp.status_code == 200:
                        return resp.json().get("name", f"sent-{int(time.time())}")
                return None
            except Exception as e:
                logger.error("Google Chat webhook error: %s", e)
                return None

        # API 模式
        if self._service:
            try:
                result = await asyncio.to_thread(
                    self._service.spaces().messages().create(
                        parent=f"spaces/{message.chat_id}",
                        body={"text": message.content},
                    ).execute
                )
                return result.get("name", "")
            except Exception as e:
                logger.error("Google Chat API send error: %s", e)
                return None

        return None

    async def edit_message(self, chat_id: str, message_id: str, new_text: str) -> bool:
        if not self._service:
            return False
        try:
            await asyncio.to_thread(
                self._service.spaces().messages().update(
                    name=message_id,
                    body={"text": new_text},
                    updateMask="text",
                ).execute
            )
            return True
        except Exception:
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        if not self._service:
            return False
        try:
            await asyncio.to_thread(
                self._service.spaces().messages().delete(name=message_id).execute
            )
            return True
        except Exception:
            return False
