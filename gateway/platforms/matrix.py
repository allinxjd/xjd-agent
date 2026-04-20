"""Matrix 平台适配器 — Matrix (Element/Synapse) 协议.

支持:
- 文本/图片/文件消息
- 端到端加密 (E2EE, 需 libolm)
- 房间管理
- 回复/引用
- Markdown 格式
- 异步轮询 (long-poll sync)
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

class MatrixAdapter(BasePlatformAdapter):
    """Matrix (Element) 适配器 — 使用 matrix-nio."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.MATRIX, config)
        self._homeserver = config.get("homeserver", "https://matrix.org")
        self._user_id = config.get("user_id", "")  # @bot:matrix.org
        self._access_token = config.get("access_token", "")
        self._password = config.get("password", "")
        self._device_id = config.get("device_id", "XJD_AGENT")
        self._store_path = config.get("store_path", "")
        self._e2ee = config.get("e2ee", False)
        self._client = None
        self._sync_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Matrix"

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
            "rich_text": True,   # Markdown / HTML
            "thread": False,
            "typing_indicator": True,
            "e2ee": self._e2ee,
        }

    async def start(self) -> None:
        """启动 Matrix 客户端."""
        try:
            from nio import AsyncClient, MatrixRoom, RoomMessageText, LoginResponse
        except ImportError:
            raise ImportError(
                "matrix-nio 未安装。请运行: pip install matrix-nio[e2e]"
            )

        # 创建客户端
        store_path = self._store_path or None
        self._client = AsyncClient(
            self._homeserver,
            self._user_id,
            device_id=self._device_id,
            store_path=store_path,
        )

        # 登录
        if self._access_token:
            self._client.access_token = self._access_token
            self._client.user_id = self._user_id
        elif self._password:
            resp = await self._client.login(self._password, device_name="XJD Agent")
            if isinstance(resp, LoginResponse):
                logger.info("Matrix login OK: %s", resp.user_id)
                self._access_token = resp.access_token
            else:
                raise RuntimeError(f"Matrix login failed: {resp}")
        else:
            raise ValueError("Matrix: access_token 或 password 必须提供一个")

        # E2EE
        if self._e2ee and store_path:
            try:
                if self._client.should_upload_keys:
                    await self._client.keys_upload()
            except Exception as e:
                logger.warning("Matrix E2EE keys_upload: %s", e)

        # 注册回调
        self._client.add_event_callback(self._on_message, RoomMessageText)

        # 启动同步
        self._running = True
        self._sync_task = asyncio.create_task(self._sync_loop())
        logger.info("Matrix adapter started: %s", self._user_id)

    async def _sync_loop(self) -> None:
        """Matrix 同步循环."""
        try:
            # 初次同步获取 next_batch
            sync_resp = await self._client.sync(timeout=0, full_state=False)
            if hasattr(sync_resp, 'next_batch'):
                self._client.next_batch = sync_resp.next_batch

            # 持续同步
            while self._running:
                try:
                    sync_resp = await self._client.sync(
                        timeout=30000,
                        full_state=False,
                    )
                    if hasattr(sync_resp, 'next_batch'):
                        self._client.next_batch = sync_resp.next_batch
                except Exception as e:
                    logger.error("Matrix sync error: %s", e)
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Matrix sync loop died: %s", e)

    async def _on_message(self, room, event) -> None:
        """处理 Matrix 消息事件."""
        # 忽略自己
        if event.sender == self._user_id:
            return

        # 判断聊天类型
        try:
            member_count = room.member_count
        except Exception:
            member_count = 2

        chat_type = ChatType.PRIVATE if member_count <= 2 else ChatType.GROUP

        # 获取显示名称
        display_name = room.user_name(event.sender) or event.sender

        platform_msg = PlatformMessage(
            platform=PlatformType.MATRIX,
            message_id=event.event_id,
            chat=PlatformChat(
                chat_id=room.room_id,
                chat_type=chat_type,
                title=room.display_name or room.room_id,
            ),
            sender=PlatformUser(
                user_id=event.sender,
                username=event.sender,
                display_name=display_name,
            ),
            message_type=MessageType.TEXT,
            content=event.body,
            raw=event,
        )

        await self._dispatch_message(platform_msg)

    async def stop(self) -> None:
        self._running = False
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
            self._sync_task = None
        if self._client:
            await self._client.close()
            self._client = None
        logger.info("Matrix adapter stopped")

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        if not self._client:
            return None

        try:
            from nio import Api

            content: dict[str, Any] = {
                "msgtype": "m.text",
                "body": message.content or "",
            }

            # Markdown → HTML
            formatted = message.metadata.get("html")
            if formatted:
                content["format"] = "org.matrix.custom.html"
                content["formatted_body"] = formatted

            # 回复
            if message.reply_to_id:
                content["m.relates_to"] = {
                    "m.in_reply_to": {"event_id": message.reply_to_id}
                }

            # 图片消息
            if message.message_type == MessageType.IMAGE and message.media_url:
                content["msgtype"] = "m.image"
                content["url"] = message.media_url

            # 文件消息
            elif message.message_type == MessageType.FILE:
                content["msgtype"] = "m.file"
                if message.media_url:
                    content["url"] = message.media_url
                filename = message.metadata.get("filename", "file")
                content["filename"] = filename
                content["body"] = filename

            resp = await self._client.room_send(
                room_id=message.chat_id,
                message_type="m.room.message",
                content=content,
            )

            if hasattr(resp, "event_id"):
                return resp.event_id
            return None

        except Exception as e:
            logger.error("Matrix send error: %s", e)
            return None

    async def send_text(self, chat_id: str, text: str, reply_to: str | None = None) -> Optional[str]:
        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            content=text,
            reply_to_id=reply_to,
        ))

    async def send_image(
        self, chat_id: str, image_url: str = "", image_data: bytes = b"", caption: str = ""
    ) -> Optional[str]:
        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            content=caption,
            message_type=MessageType.IMAGE,
            media_url=image_url,
        ))

    async def edit_message(self, chat_id: str, message_id: str, new_content: str) -> bool:
        """编辑消息 (Matrix m.replace)."""
        if not self._client:
            return False

        try:
            content = {
                "msgtype": "m.text",
                "body": f"* {new_content}",
                "m.new_content": {
                    "msgtype": "m.text",
                    "body": new_content,
                },
                "m.relates_to": {
                    "rel_type": "m.replace",
                    "event_id": message_id,
                },
            }

            await self._client.room_send(
                room_id=chat_id,
                message_type="m.room.message",
                content=content,
            )
            return True

        except Exception as e:
            logger.error("Matrix edit error: %s", e)
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """撤回消息 (Matrix redact)."""
        if not self._client:
            return False

        try:
            await self._client.room_redact(
                room_id=chat_id,
                event_id=message_id,
                reason="Deleted by bot",
            )
            return True
        except Exception as e:
            logger.error("Matrix delete error: %s", e)
            return False

    async def send_typing(self, chat_id: str, typing: bool = True, timeout: int = 5000) -> None:
        """发送正在输入状态."""
        if self._client:
            try:
                await self._client.room_typing(chat_id, typing, timeout=timeout)
            except Exception:
                pass

    async def health_check(self) -> dict[str, Any]:
        return {
            "platform": "matrix",
            "name": self.name,
            "running": self._running,
            "user_id": self._user_id,
            "homeserver": self._homeserver,
        }
