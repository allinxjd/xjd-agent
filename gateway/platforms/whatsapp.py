"""WhatsApp 平台适配器 — WhatsApp Business Cloud API.

支持:
- 文本/图片/文件消息
- 模板消息 (Template Messages)
- 交互按钮 (Interactive Messages)
- Webhook 验证
- 已读回执
"""

from __future__ import annotations

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

class WhatsAppAdapter(BasePlatformAdapter):
    """WhatsApp Business Cloud API 适配器."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.WHATSAPP, config)
        self._access_token = config.get("access_token", "")
        self._phone_number_id = config.get("phone_number_id", "")
        self._verify_token = config.get("verify_token", "xjd-agent-whatsapp")
        self._app_secret = config.get("app_secret", "")
        self._api_version = config.get("api_version", "v18.0")
        self._base_url = f"https://graph.facebook.com/{self._api_version}"
        self._webhook_server = None

    @property
    def name(self) -> str:
        return "WhatsApp"

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
            "reaction": True,
            "template": True,
            "interactive": True,
            "location": True,
        }

    async def start(self) -> None:
        """启动 Webhook 服务器接收消息."""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp 未安装。请运行: pip install aiohttp")

        app = web.Application()
        app.router.add_get("/webhook/whatsapp", self._verify_webhook)
        app.router.add_post("/webhook/whatsapp", self._handle_webhook)

        port = self._config.get("webhook_port", 8443)
        runner = web.AppRunner(app)
        await runner.setup()
        self._webhook_server = web.TCPSite(runner, "0.0.0.0", port)
        await self._webhook_server.start()
        logger.info("WhatsApp webhook listening on port %d", port)

    async def stop(self) -> None:
        if self._webhook_server:
            await self._webhook_server.stop()
            self._webhook_server = None

    async def _verify_webhook(self, request):
        """WhatsApp Webhook 验证 (GET)."""
        from aiohttp import web

        mode = request.query.get("hub.mode")
        token = request.query.get("hub.verify_token")
        challenge = request.query.get("hub.challenge")

        if mode == "subscribe" and token == self._verify_token:
            logger.info("WhatsApp webhook verified")
            return web.Response(text=challenge)

        return web.Response(status=403, text="Forbidden")

    async def _handle_webhook(self, request):
        """处理 WhatsApp Webhook 消息 (POST)."""
        from aiohttp import web

        body = await request.read()

        # 验证签名
        if self._app_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(
                self._app_secret.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                return web.Response(status=403)

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400)

        # 解析消息
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                contacts = value.get("contacts", [])

                contact_map = {}
                for c in contacts:
                    contact_map[c.get("wa_id", "")] = c.get("profile", {}).get("name", "")

                for msg in messages:
                    await self._process_message(msg, contact_map)

        return web.Response(text="OK")

    async def _process_message(self, msg: dict, contacts: dict) -> None:
        """处理单条消息."""
        msg_type = msg.get("type", "")
        from_number = msg.get("from", "")
        msg_id = msg.get("id", "")
        timestamp = msg.get("timestamp", "")

        text = ""
        message_type = MessageType.TEXT

        if msg_type == "text":
            text = msg.get("text", {}).get("body", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            text = msg.get("image", {}).get("caption", "")
        elif msg_type == "audio":
            message_type = MessageType.VOICE
        elif msg_type == "video":
            message_type = MessageType.VIDEO
        elif msg_type == "document":
            message_type = MessageType.FILE
            text = msg.get("document", {}).get("filename", "")
        elif msg_type == "location":
            loc = msg.get("location", {})
            text = f"📍 {loc.get('latitude')}, {loc.get('longitude')}"
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            itype = interactive.get("type", "")
            if itype == "button_reply":
                text = interactive.get("button_reply", {}).get("title", "")
            elif itype == "list_reply":
                text = interactive.get("list_reply", {}).get("title", "")
        else:
            return

        platform_msg = PlatformMessage(
            platform=PlatformType.WHATSAPP,
            message_id=msg_id,
            chat=PlatformChat(
                chat_id=from_number,
                chat_type=ChatType.PRIVATE,
                title=contacts.get(from_number, from_number),
            ),
            sender=PlatformUser(
                user_id=from_number,
                username=from_number,
                display_name=contacts.get(from_number, from_number),
            ),
            message_type=message_type,
            content=text,
            raw=msg,
        )

        await self._dispatch_message(platform_msg)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import httpx

            url = f"{self._base_url}/{self._phone_number_id}/messages"
            headers = {
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            }

            body: dict[str, Any] = {
                "messaging_product": "whatsapp",
                "to": message.chat_id,
            }

            # 交互式消息
            if message.metadata.get("interactive"):
                body["type"] = "interactive"
                body["interactive"] = message.metadata["interactive"]
            # 模板消息
            elif message.metadata.get("template"):
                body["type"] = "template"
                body["template"] = message.metadata["template"]
            # 普通文本
            else:
                body["type"] = "text"
                body["text"] = {"body": message.content or ""}

            # 引用回复
            if message.reply_to_id:
                body["context"] = {"message_id": message.reply_to_id}

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=headers, json=body)
                resp.raise_for_status()
                data = resp.json()

                msg_id = data.get("messages", [{}])[0].get("id", "")
                return msg_id

        except Exception as e:
            logger.error("WhatsApp send error: %s", e)
            return None

    async def send_text(self, chat_id: str, text: str) -> Optional[str]:
        return await self.send_message(OutgoingMessage(chat_id=chat_id, content=text))

    async def send_image(self, chat_id: str, image_url: str, caption: str = "") -> Optional[str]:
        try:
            import httpx

            url = f"{self._base_url}/{self._phone_number_id}/messages"
            body = {
                "messaging_product": "whatsapp",
                "to": chat_id,
                "type": "image",
                "image": {"link": image_url, "caption": caption},
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
                resp.raise_for_status()
                data = resp.json()
                return data.get("messages", [{}])[0].get("id", "")
        except Exception as e:
            logger.error("WhatsApp send image error: %s", e)
            return None

    async def mark_read(self, message_id: str) -> None:
        """标记消息已读."""
        try:
            import httpx

            url = f"{self._base_url}/{self._phone_number_id}/messages"
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._access_token}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "messaging_product": "whatsapp",
                        "status": "read",
                        "message_id": message_id,
                    },
                )
        except Exception:
            pass

    async def health_check(self) -> bool:
        return self._webhook_server is not None
