"""钉钉 (DingTalk) 适配器 — 基于 dingtalk-stream.

功能:
- Stream 模式接收消息 (推荐，无需公网地址)
- 文本/图片/文件/Markdown 消息处理
- 群聊 @机器人 触发
- 互动式卡片发送
- Webhook outgoing 模式 (备用)

依赖: pip install "xjd-agent[dingtalk]"
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    ChatType,
    EventType,
    MessageType,
    OutgoingMessage,
    PlatformChat,
    PlatformEvent,
    PlatformMessage,
    PlatformType,
    PlatformUser,
)

logger = logging.getLogger(__name__)

class DingTalkAdapter(BasePlatformAdapter):
    """钉钉机器人适配器.

    配置 (config dict):
        app_key: str          # 应用 AppKey
        app_secret: str       # 应用 AppSecret
        robot_code: str       # 机器人编码 (可选，Stream 模式自动获取)
        mode: str             # "stream" (推荐) | "webhook"
        webhook_token: str    # Webhook 模式下的 outgoing token

    钉钉开放平台配置:
        1. 创建企业内部应用 / 第三方企业应用
        2. 添加机器人能力
        3. Stream 模式无需公网地址，直接连接
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.DINGTALK, config)
        self._app_key = config.get("app_key", "")
        self._app_secret = config.get("app_secret", "")
        self._robot_code = config.get("robot_code", "")
        self._mode = config.get("mode", "stream")
        self._access_token: str = ""
        self._token_expire_time: float = 0
        self._stream_client = None

    @property
    def name(self) -> str:
        return "钉钉"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "voice": False,
            "video": False,
            "file": True,
            "rich_text": True,      # Markdown
            "interactive": True,     # 互动卡片
            "edit_message": False,
            "delete_message": False,
            "reaction": False,
            "thread": False,
            "typing_indicator": False,
        }

    async def _get_access_token(self) -> str:
        """获取钉钉 access_token."""
        now = time.time()
        if self._access_token and now < self._token_expire_time:
            return self._access_token

        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.dingtalk.com/v1.0/oauth2/accessToken",
                json={
                    "appKey": self._app_key,
                    "appSecret": self._app_secret,
                },
            )
            data = resp.json()
            if "accessToken" not in data:
                raise RuntimeError(f"获取钉钉 token 失败: {data}")

            self._access_token = data["accessToken"]
            self._token_expire_time = now + data.get("expireIn", 7200) - 300
            return self._access_token

    async def start(self) -> None:
        """启动钉钉适配器."""
        if not self._app_key or not self._app_secret:
            raise ValueError("钉钉 app_key 和 app_secret 未配置")

        if self._mode == "stream":
            await self._start_stream()
        else:
            await self._start_webhook()

        self._running = True
        logger.info("钉钉适配器已启动 (mode=%s)", self._mode)

    async def _start_stream(self) -> None:
        """Stream 模式启动."""
        try:
            from dingtalk_stream import AckMessage, ChatbotMessage
            import dingtalk_stream
        except ImportError:
            raise ImportError(
                '钉钉 Stream 依赖未安装。请运行: pip install "xjd-agent[dingtalk]"'
            )

        credential = dingtalk_stream.Credential(self._app_key, self._app_secret)
        client = dingtalk_stream.DingTalkStreamClient(credential)

        # 注册消息回调
        class ChatbotHandler(dingtalk_stream.ChatbotHandler):
            def __init__(self, adapter: DingTalkAdapter):
                super().__init__()
                self._adapter = adapter

            async def process(self, callback: ChatbotMessage):
                await self._adapter._handle_stream_message(callback)
                return AckMessage.STATUS_OK, "ok"

        client.register_callback_handler(
            dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
            ChatbotHandler(self),
        )

        # 在后台启动 stream client
        self._stream_client = client
        asyncio.create_task(asyncio.to_thread(client.start_forever))

    async def _start_webhook(self) -> None:
        """Webhook 模式启动 (备用)."""
        # Webhook 模式需要外部 HTTP 服务器
        logger.info("钉钉 Webhook 模式: 等待外部 HTTP 服务器转发消息")

    async def stop(self) -> None:
        """停止钉钉适配器."""
        self._running = False
        if self._stream_client:
            # dingtalk_stream 没有优雅关闭接口
            self._stream_client = None
        logger.info("钉钉适配器已停止")

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息到钉钉."""
        import httpx

        token = await self._get_access_token()

        if message.message_type == MessageType.RICH_TEXT:
            # Markdown 消息
            msg_body = {
                "msgType": "sampleMarkdown",
                "sampleMarkdown": {
                    "title": "小巨蛋智能体",
                    "text": message.content,
                },
            }
        elif message.message_type == MessageType.IMAGE:
            msg_body = {
                "msgType": "sampleImageMsg",
                "sampleImageMsg": {
                    "photoURL": message.media_url,
                },
            }
        elif message.message_type == MessageType.INTERACTIVE:
            # 互动卡片
            msg_body = {
                "msgType": "actionCard",
                "actionCard": message.interactive or {},
            }
        else:
            # 纯文本
            msg_body = {
                "msgType": "sampleText",
                "sampleText": {
                    "content": message.content,
                },
            }

        # 构建请求
        body: dict[str, Any] = {
            "robotCode": self._robot_code,
            **msg_body,
        }

        # 单聊 or 群聊
        if message.metadata.get("webhook_url"):
            # 通过 Webhook URL 回复
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    message.metadata["webhook_url"],
                    json=body,
                )
                data = resp.json()
        else:
            # 通过 API 发送
            is_group = message.metadata.get("is_group", False)
            if is_group:
                body["openConversationId"] = message.chat_id
                api_path = "/v1.0/robot/groupMessages/send"
            else:
                body["userIds"] = [message.chat_id]
                api_path = "/v1.0/robot/oToMessages/batchSend"

            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://api.dingtalk.com{api_path}",
                    json=body,
                    headers={
                        "x-acs-dingtalk-access-token": token,
                        "Content-Type": "application/json",
                    },
                )
                data = resp.json()

        if "processQueryKey" in data:
            return data["processQueryKey"]
        return data.get("requestId", "")

    # ── 消息处理 ──────────────────────────────────────────────

    async def _handle_stream_message(self, callback: Any) -> None:
        """处理 Stream 模式收到的消息."""
        # 解析消息
        sender = PlatformUser(
            user_id=callback.sender_id or "",
            username=callback.sender_nick or "",
            display_name=callback.sender_nick or "",
        )

        is_group = callback.conversation_type == "2"
        chat = PlatformChat(
            chat_id=callback.conversation_id or "",
            chat_type=ChatType.GROUP if is_group else ChatType.PRIVATE,
            title=callback.conversation_title or "",
            platform=PlatformType.DINGTALK,
        )

        content = callback.text.content if hasattr(callback.text, "content") else str(callback.text or "")
        # 去掉 @机器人 文本
        content = content.strip()

        platform_msg = PlatformMessage(
            message_id=callback.message_id or "",
            platform=PlatformType.DINGTALK,
            chat=chat,
            sender=sender,
            message_type=MessageType.TEXT,
            content=content,
            timestamp=time.time(),
            metadata={
                "webhook_url": getattr(callback, "session_webhook", ""),
                "is_group": is_group,
            },
            raw=callback,
        )

        await self._dispatch_message(platform_msg)

    async def handle_webhook_request(self, body: dict[str, Any]) -> dict[str, Any]:
        """处理 Webhook outgoing 模式的请求 (由外部 HTTP 服务器调用)."""
        msg_type = body.get("msgtype", "text")

        sender = PlatformUser(
            user_id=body.get("senderId", ""),
            username=body.get("senderNick", ""),
            display_name=body.get("senderNick", ""),
        )

        is_group = body.get("conversationType") == "2"
        chat = PlatformChat(
            chat_id=body.get("conversationId", ""),
            chat_type=ChatType.GROUP if is_group else ChatType.PRIVATE,
            title=body.get("conversationTitle", ""),
            platform=PlatformType.DINGTALK,
        )

        content = ""
        message_type = MessageType.TEXT
        if msg_type == "text":
            content = body.get("text", {}).get("content", "").strip()
        elif msg_type == "richText":
            message_type = MessageType.RICH_TEXT
            rich_text = body.get("content", {}).get("richText", [])
            for section in rich_text:
                for item in section.get("text", []):
                    content += item.get("text", "")
        elif msg_type == "picture":
            message_type = MessageType.IMAGE
            content = body.get("content", {}).get("downloadCode", "")

        platform_msg = PlatformMessage(
            message_id=body.get("msgId", ""),
            platform=PlatformType.DINGTALK,
            chat=chat,
            sender=sender,
            message_type=message_type,
            content=content,
            metadata={
                "session_webhook": body.get("sessionWebhook", ""),
                "is_group": is_group,
            },
            timestamp=time.time(),
            raw=body,
        )

        await self._dispatch_message(platform_msg)
        return {"msgtype": "empty"}
