"""LINE 平台适配器 — LINE Messaging API.

支持:
- 文本/图片/视频/音频/文件/位置消息
- Flex Message (富消息卡片)
- Quick Reply (快捷回复)
- Rich Menu (底部菜单)
- Webhook 签名验证
- 群组/单聊
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
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

class LineAdapter(BasePlatformAdapter):
    """LINE Messaging API 适配器."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.LINE, config)
        self._channel_secret = config.get("channel_secret", "")
        self._channel_access_token = config.get("channel_access_token", "")
        self._api_base = "https://api.line.me/v2"
        self._webhook_server = None

    @property
    def name(self) -> str:
        return "LINE"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "file": True,
            "voice": True,
            "video": True,
            "edit_message": False,
            "delete_message": False,
            "reply": True,
            "reaction": False,
            "rich_text": False,
            "interactive": True,  # Flex Message
            "location": True,
            "quick_reply": True,
            "rich_menu": True,
        }

    async def start(self) -> None:
        """启动 LINE Webhook 服务器."""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp 未安装。请运行: pip install aiohttp")

        app = web.Application()
        app.router.add_post("/webhook/line", self._handle_webhook)

        port = self._config.get("webhook_port", 8444)
        runner = web.AppRunner(app)
        await runner.setup()
        self._webhook_server = web.TCPSite(runner, "0.0.0.0", port)
        await self._webhook_server.start()
        self._running = True
        logger.info("LINE webhook listening on port %d", port)

    async def stop(self) -> None:
        if self._webhook_server:
            await self._webhook_server.stop()
            self._webhook_server = None
        self._running = False

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        """验证 LINE Webhook 签名."""
        hash_val = hmac.new(
            self._channel_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        expected = base64.b64encode(hash_val).decode("utf-8")
        return hmac.compare_digest(signature, expected)

    async def _handle_webhook(self, request):
        """处理 LINE Webhook 事件."""
        from aiohttp import web

        body = await request.read()

        # 验证签名
        signature = request.headers.get("X-Line-Signature", "")
        if self._channel_secret and not self._verify_signature(body, signature):
            return web.Response(status=403, text="Invalid signature")

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400)

        for event in data.get("events", []):
            await self._process_event(event)

        return web.Response(text="OK")

    async def _process_event(self, event: dict) -> None:
        """处理单个 LINE 事件."""
        event_type = event.get("type", "")

        if event_type != "message":
            return

        source = event.get("source", {})
        source_type = source.get("type", "")
        reply_token = event.get("replyToken", "")

        # 用户信息
        user_id = source.get("userId", "")
        profile = await self._get_profile(user_id)

        # 聊天信息
        if source_type == "group":
            chat_id = source.get("groupId", "")
            chat_type = ChatType.GROUP
        elif source_type == "room":
            chat_id = source.get("roomId", "")
            chat_type = ChatType.GROUP
        else:
            chat_id = user_id
            chat_type = ChatType.PRIVATE

        # 消息类型
        msg = event.get("message", {})
        msg_type = msg.get("type", "")
        msg_id = msg.get("id", "")

        text = ""
        message_type = MessageType.TEXT

        if msg_type == "text":
            text = msg.get("text", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
        elif msg_type == "video":
            message_type = MessageType.VIDEO
        elif msg_type == "audio":
            message_type = MessageType.VOICE
        elif msg_type == "file":
            message_type = MessageType.FILE
            text = msg.get("fileName", "")
        elif msg_type == "location":
            text = f"📍 {msg.get('address', '')} ({msg.get('latitude')}, {msg.get('longitude')})"
        elif msg_type == "sticker":
            text = f"[贴图: {msg.get('packageId')}/{msg.get('stickerId')}]"
        else:
            return

        platform_msg = PlatformMessage(
            platform=PlatformType.LINE,
            message_id=msg_id,
            chat=PlatformChat(
                chat_id=chat_id,
                chat_type=chat_type,
                title=chat_id,
            ),
            sender=PlatformUser(
                user_id=user_id,
                username=profile.get("displayName", user_id),
                display_name=profile.get("displayName", user_id),
                avatar_url=profile.get("pictureUrl", ""),
            ),
            message_type=message_type,
            text=text,
            metadata={"reply_token": reply_token},
            raw=event,
        )

        await self._dispatch_message(platform_msg)

    async def _get_profile(self, user_id: str) -> dict:
        """获取用户资料."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._api_base}/bot/profile/{user_id}",
                    headers={"Authorization": f"Bearer {self._channel_access_token}"},
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning("LINE get_profile error: %s", e)
        return {}

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        """发送消息 (Push API)."""
        try:
            import httpx

            url = f"{self._api_base}/bot/message/push"
            headers = {
                "Authorization": f"Bearer {self._channel_access_token}",
                "Content-Type": "application/json",
            }

            # 构造消息
            messages = []

            if message.metadata.get("flex"):
                # Flex Message
                messages.append({
                    "type": "flex",
                    "altText": message.content or "消息",
                    "contents": message.metadata["flex"],
                })
            elif message.metadata.get("quick_reply"):
                messages.append({
                    "type": "text",
                    "text": message.content or "",
                    "quickReply": {"items": message.metadata["quick_reply"]},
                })
            elif message.message_type == MessageType.IMAGE and message.media_url:
                messages.append({
                    "type": "image",
                    "originalContentUrl": message.media_url,
                    "previewImageUrl": message.media_url,
                })
                if message.content:
                    messages.insert(0, {"type": "text", "text": message.content})
            else:
                # 文本消息，LINE 单条最大 5000 字符
                text = message.content or ""
                while text:
                    chunk = text[:5000]
                    messages.append({"type": "text", "text": chunk})
                    text = text[5000:]

            # Reply 或 Push
            reply_token = message.metadata.get("reply_token")
            if reply_token:
                url = f"{self._api_base}/bot/message/reply"
                body = {"replyToken": reply_token, "messages": messages[:5]}
            else:
                body = {"to": message.chat_id, "messages": messages[:5]}

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                return "sent"

        except Exception as e:
            logger.error("LINE send error: %s", e)
            return None

    async def send_text(self, chat_id: str, text: str, reply_to: str | None = None) -> Optional[str]:
        return await self.send_message(OutgoingMessage(chat_id=chat_id, content=text))

    async def send_image(
        self, chat_id: str, image_url: str = "", image_data: bytes = b"", caption: str = ""
    ) -> Optional[str]:
        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            content=caption,
            message_type=MessageType.IMAGE,
            media_url=image_url,
        ))

    async def health_check(self) -> dict[str, Any]:
        return {
            "platform": "line",
            "name": self.name,
            "running": self._running,
        }
