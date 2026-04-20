"""Web 平台适配器 — 将 HTTP/WebSocket 用户纳入 Gateway 统一管理.

Web 前端通过 aiohttp WebSocket 连接，本适配器负责:
1. 管理 WebSocket 连接生命周期
2. 将 WebSocket 消息转换为统一 PlatformMessage
3. 将 OutgoingMessage 通过 WebSocket 发回前端
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Optional

from .base import (
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

class WebPlatformAdapter(BasePlatformAdapter):
    """Web (HTTP/WebSocket) 平台适配器.

    用法:
        adapter = WebPlatformAdapter(config={"host": "0.0.0.0", "port": 8080})
        adapter.on_message(handler)
        await adapter.start()
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        super().__init__(PlatformType.WEB, config or {})
        self._host = self._config.get("host", "0.0.0.0")
        self._port = self._config.get("port", 8080)
        self._connections: dict[str, Any] = {}  # session_id → ws
        self._sessions: dict[str, dict[str, Any]] = {}
        self._app = None
        self._runner = None

    @property
    def name(self) -> str:
        return "Web"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "voice": False,
            "video": False,
            "file": True,
            "rich_text": True,
            "interactive": False,
            "edit_message": True,
            "delete_message": True,
            "reaction": False,
            "thread": False,
            "typing_indicator": True,
        }

    async def start(self) -> None:
        """启动 WebSocket 服务器."""
        self._running = True
        self._bot_user = PlatformUser(
            user_id="xjd-agent",
            username="XJD Agent",
            display_name="小巨蛋智能体",
            is_bot=True,
        )
        logger.info("Web adapter started (ws://%s:%d)", self._host, self._port)

    async def stop(self) -> None:
        """停止服务并关闭所有连接."""
        self._running = False
        # 关闭所有 WebSocket 连接
        for sid, ws in list(self._connections.items()):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._sessions.clear()
        logger.info("Web adapter stopped")

    async def send_message(self, message: OutgoingMessage) -> str:
        """通过 WebSocket 发送消息."""
        ws = self._connections.get(message.chat_id)
        if not ws:
            logger.warning("No WebSocket connection for chat_id: %s", message.chat_id)
            return ""

        msg_id = str(uuid.uuid4())[:12]
        payload = {
            "type": "complete",
            "message_id": msg_id,
            "content": message.content,
        }

        if message.media_url:
            payload["media_url"] = message.media_url
        if message.reply_to_id:
            payload["reply_to_id"] = message.reply_to_id

        try:
            await ws.send_json(payload)
            return msg_id
        except Exception as e:
            logger.error("Failed to send WebSocket message: %s", e)
            return ""

    async def edit_message(self, chat_id: str, message_id: str, new_content: str) -> bool:
        """编辑已发送的消息."""
        ws = self._connections.get(chat_id)
        if not ws:
            return False
        try:
            await ws.send_json({
                "type": "edit",
                "message_id": message_id,
                "content": new_content,
            })
            return True
        except Exception:
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """删除消息."""
        ws = self._connections.get(chat_id)
        if not ws:
            return False
        try:
            await ws.send_json({
                "type": "delete",
                "message_id": message_id,
            })
            return True
        except Exception:
            return False

    # ── WebSocket 会话管理 ──

    def register_connection(self, session_id: str, ws: Any) -> None:
        """注册新的 WebSocket 连接."""
        self._connections[session_id] = ws
        self._sessions[session_id] = {
            "connected_at": time.time(),
            "message_count": 0,
        }

    def unregister_connection(self, session_id: str) -> None:
        """注销 WebSocket 连接."""
        self._connections.pop(session_id, None)
        self._sessions.pop(session_id, None)

    async def handle_incoming(self, session_id: str, data: str) -> None:
        """处理收到的 WebSocket 消息, 转换为 PlatformMessage 并分发."""
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            payload = {"message": data}

        content = payload.get("message", "")
        if not content:
            return

        # 更新会话统计
        if session_id in self._sessions:
            self._sessions[session_id]["message_count"] += 1

        msg = PlatformMessage(
            message_id=str(uuid.uuid4())[:12],
            platform=PlatformType.WEB,
            chat=PlatformChat(
                chat_id=session_id,
                chat_type=ChatType.PRIVATE,
                platform=PlatformType.WEB,
            ),
            sender=PlatformUser(
                user_id=f"web_{session_id}",
                username="Web User",
            ),
            message_type=MessageType.TEXT,
            content=content,
            timestamp=time.time(),
        )

        await self._dispatch_message(msg)

    async def send_typing(self, session_id: str) -> None:
        """发送打字指示器."""
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json({"type": "typing"})
            except Exception:
                pass

    async def send_stream_chunk(self, session_id: str, chunk: str) -> None:
        """发送流式文本块."""
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json({"type": "stream", "content": chunk})
            except Exception:
                pass
