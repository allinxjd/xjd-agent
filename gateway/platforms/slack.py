"""Slack 平台适配器 — slack-bolt.

支持:
- 私聊 + 频道消息
- Slack Events API (Socket Mode)
- Block Kit 富消息
- Slash commands
- 线程回复
- @bot 提及
- 文件上传
"""

from __future__ import annotations

import asyncio
import logging
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

class SlackAdapter(BasePlatformAdapter):
    """Slack 适配器 — 使用 slack-bolt (Socket Mode)."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.SLACK, config)
        self._bot_token = config.get("bot_token", "")
        self._app_token = config.get("app_token", "")  # Socket Mode
        self._signing_secret = config.get("signing_secret", "")
        self._app = None
        self._handler = None
        self._bot_id: Optional[str] = None

    @property
    def name(self) -> str:
        return "Slack"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "file": True,
            "voice": False,
            "video": False,
            "edit_message": True,
            "delete_message": True,
            "reply": True,
            "reaction": True,
            "thread": True,
            "button": True,
            "block_kit": True,
        }

    async def start(self) -> None:
        try:
            from slack_bolt.async_app import AsyncApp
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
        except ImportError:
            raise ImportError("slack-bolt 未安装。请运行: pip install slack-bolt[async]")

        self._app = AsyncApp(
            token=self._bot_token,
            signing_secret=self._signing_secret,
        )

        # 获取 bot user id
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            client = AsyncWebClient(token=self._bot_token)
            auth = await client.auth_test()
            self._bot_id = auth["user_id"]
            logger.info("Slack bot: %s (%s)", auth.get("user", ""), self._bot_id)
        except Exception as e:
            logger.warning("Slack auth_test failed: %s", e)

        @self._app.event("message")
        async def handle_message(event, say, client):
            # 忽略 bot 自己的消息
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return

            user_id = event.get("user", "")
            channel = event.get("channel", "")
            text = event.get("text", "")
            thread_ts = event.get("thread_ts", "")
            ts = event.get("ts", "")

            # 去掉 @bot 提及
            if self._bot_id:
                text = text.replace(f"<@{self._bot_id}>", "").strip()

            # 判断聊天类型
            channel_type = event.get("channel_type", "")
            if channel_type == "im":
                chat_type = ChatType.PRIVATE
            else:
                chat_type = ChatType.GROUP

            # 获取用户信息
            username = user_id
            display_name = user_id
            try:
                user_info = await client.users_info(user=user_id)
                if user_info["ok"]:
                    profile = user_info["user"]["profile"]
                    username = user_info["user"].get("name", user_id)
                    display_name = profile.get("display_name") or profile.get("real_name", username)
            except Exception:
                pass

            platform_msg = PlatformMessage(
                platform=PlatformType.SLACK,
                message_id=ts,
                chat=PlatformChat(
                    chat_id=channel,
                    chat_type=chat_type,
                    title=channel,
                ),
                sender=PlatformUser(
                    user_id=user_id,
                    username=username,
                    display_name=display_name,
                ),
                message_type=MessageType.TEXT,
                content=text,
                raw=event,
            )

            if thread_ts:
                platform_msg.metadata["thread_ts"] = thread_ts

            # 处理文件
            files = event.get("files", [])
            if files:
                for f in files:
                    if f.get("mimetype", "").startswith("image/"):
                        platform_msg.message_type = MessageType.IMAGE
                        platform_msg.media_url = f.get("url_private", "")
                    break

            await self._dispatch_message(platform_msg)

        @self._app.event("app_mention")
        async def handle_mention(event, say, client):
            await handle_message(event, say, client)

        # Socket Mode 启动
        if self._app_token:
            self._handler = AsyncSocketModeHandler(self._app, self._app_token)
            asyncio.create_task(self._handler.start_async())
            logger.info("Slack adapter started (Socket Mode)")
        else:
            logger.warning("Slack: no app_token, Socket Mode disabled")

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()
            self._handler = None

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        if not self._app:
            return None

        try:
            kwargs: dict[str, Any] = {
                "channel": message.chat_id,
                "text": message.content or "",
            }

            # 线程回复
            thread_ts = message.metadata.get("thread_ts")
            if thread_ts:
                kwargs["thread_ts"] = thread_ts

            # Block Kit
            blocks = message.metadata.get("blocks")
            if blocks:
                kwargs["blocks"] = blocks

            result = await self._app.client.chat_postMessage(**kwargs)
            return result.get("ts", "")

        except Exception as e:
            logger.error("Slack send error: %s", e)
            return None

    async def send_text(self, chat_id: str, text: str) -> Optional[str]:
        return await self.send_message(OutgoingMessage(chat_id=chat_id, content=text))

    async def send_image(self, chat_id: str, image_url: str, caption: str = "") -> Optional[str]:
        if not self._app:
            return None
        try:
            blocks = [{
                "type": "image",
                "image_url": image_url,
                "alt_text": caption or "image",
            }]
            if caption:
                blocks.insert(0, {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": caption},
                })

            result = await self._app.client.chat_postMessage(
                channel=chat_id,
                text=caption or "Image",
                blocks=blocks,
            )
            return result.get("ts", "")
        except Exception as e:
            logger.error("Slack send image error: %s", e)
            return None

    async def edit_message(self, chat_id: str, message_id: str, new_text: str) -> bool:
        if not self._app:
            return False
        try:
            await self._app.client.chat_update(
                channel=chat_id, ts=message_id, text=new_text,
            )
            return True
        except Exception as e:
            logger.error("Slack edit error: %s", e)
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        if not self._app:
            return False
        try:
            await self._app.client.chat_delete(channel=chat_id, ts=message_id)
            return True
        except Exception:
            return False

    async def health_check(self) -> bool:
        if not self._app:
            return False
        try:
            result = await self._app.client.auth_test()
            return result.get("ok", False)
        except Exception:
            return False
