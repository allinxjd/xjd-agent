"""消息平台适配器基类 — 所有消息平台 (微信/飞书/钉钉/Telegram/Discord/Slack 等) 都实现此基类，
通过 Gateway WebSocket 控制面统一管理。

架构:
  Gateway Server (ws://127.0.0.1:18789)
    ├── WeChatAdapter
    ├── FeishuAdapter
    ├── DingTalkAdapter
    ├── TelegramAdapter
    └── ...

每个 Adapter 负责:
1. 平台认证 (bot token / app secret / webhook secret)
2. 接收消息 → 转换为统一 PlatformMessage
3. 发送回复 → 将 AgentResponse 转换为平台格式
4. 事件处理 (好友请求、群邀请、at 提及等)
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

class PlatformType(str, Enum):
    """支持的消息平台."""

    WECHAT = "wechat"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    MATRIX = "matrix"
    WHATSAPP = "whatsapp"
    LINE = "line"
    EMAIL = "email"
    SIGNAL = "signal"
    IMESSAGE = "imessage"
    IRC = "irc"
    GOOGLE_CHAT = "google_chat"
    TEAMS = "teams"
    SMS = "sms"
    FACEBOOK = "facebook"
    TWITTER = "twitter"
    REDDIT = "reddit"
    WEB = "web"
    API = "api"

class MessageType(str, Enum):
    """消息类型."""

    TEXT = "text"
    IMAGE = "image"
    VOICE = "voice"
    VIDEO = "video"
    FILE = "file"
    LOCATION = "location"
    LINK = "link"
    RICH_TEXT = "rich_text"       # 富文本 (markdown / HTML)
    INTERACTIVE = "interactive"   # 交互式卡片
    SYSTEM = "system"             # 系统消息

class ChatType(str, Enum):
    """聊天类型."""

    PRIVATE = "private"   # 私聊
    GROUP = "group"       # 群聊
    CHANNEL = "channel"   # 频道

class EventType(str, Enum):
    """平台事件类型."""

    MESSAGE = "message"
    MESSAGE_EDIT = "message_edit"
    MESSAGE_DELETE = "message_delete"
    REACTION = "reaction"
    FRIEND_REQUEST = "friend_request"
    GROUP_INVITE = "group_invite"
    GROUP_JOIN = "group_join"
    GROUP_LEAVE = "group_leave"
    MENTION = "mention"
    TYPING = "typing"
    STATUS = "status"

@dataclass
class PlatformUser:
    """统一用户模型."""

    user_id: str
    username: str = ""
    display_name: str = ""
    avatar_url: str = ""
    is_bot: bool = False
    raw: Any = None  # 平台原始用户对象

@dataclass
class PlatformChat:
    """统一聊天对象."""

    chat_id: str
    chat_type: ChatType = ChatType.PRIVATE
    title: str = ""
    platform: PlatformType = PlatformType.WEB
    raw: Any = None

@dataclass
class PlatformMessage:
    """统一消息模型 — 所有平台消息转换为此格式.

    这是 Gateway 内部流转的标准消息格式。
    平台适配器负责将原始消息转换为 PlatformMessage，
    Agent 处理后将回复转换回平台格式。
    """

    message_id: str
    platform: PlatformType
    chat: PlatformChat
    sender: PlatformUser
    message_type: MessageType = MessageType.TEXT
    content: str = ""
    media_url: str = ""  # 图片/语音/视频 URL
    media_data: bytes = b""  # 二进制内容
    reply_to_id: Optional[str] = None  # 回复的消息 ID
    mentions: list[str] = field(default_factory=list)  # @提及的用户 ID
    metadata: dict[str, Any] = field(default_factory=dict)  # 额外数据
    timestamp: float = 0.0
    raw: Any = None  # 平台原始消息对象

@dataclass
class OutgoingMessage:
    """待发送的消息."""

    chat_id: str
    content: str = ""
    message_type: MessageType = MessageType.TEXT
    reply_to_id: Optional[str] = None
    media_url: str = ""
    media_data: bytes = b""
    interactive: Optional[dict[str, Any]] = None  # 交互式卡片
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class PlatformEvent:
    """平台事件."""

    event_type: EventType
    platform: PlatformType
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

# 消息处理器类型
MessageHandler = Callable[[PlatformMessage], Coroutine[Any, Any, None]]
EventHandler = Callable[[PlatformEvent], Coroutine[Any, Any, None]]

class BasePlatformAdapter(ABC):
    """消息平台适配器基类.

    所有平台适配器都必须实现此基类。

    生命周期:
        adapter = TelegramAdapter(config)
        adapter.on_message(handler)  # 注册消息处理器
        await adapter.start()        # 启动 (连接平台、开始监听)
        ...
        await adapter.stop()         # 停止

    消息流:
        平台 → Adapter.receive → PlatformMessage → Handler → AgentEngine
        AgentEngine → OutgoingMessage → Adapter.send → 平台
    """

    def __init__(
        self,
        platform_type: PlatformType,
        config: dict[str, Any],
    ) -> None:
        self.platform_type = platform_type
        self._config = config
        self._message_handlers: list[MessageHandler] = []
        self._event_handlers: list[EventHandler] = []
        self._running = False
        self._bot_user: Optional[PlatformUser] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """适配器名称 (显示用)."""

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def bot_user(self) -> Optional[PlatformUser]:
        """当前 Bot 的用户信息."""
        return self._bot_user

    # ── 生命周期 ──────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """启动适配器 — 连接平台、开始监听消息.

        子类实现此方法进行平台认证、WebSocket 连接、Webhook 注册等。
        """

    @abstractmethod
    async def stop(self) -> None:
        """停止适配器 — 断开连接、清理资源."""

    # ── 消息发送 ──────────────────────────────────────────────

    @abstractmethod
    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息到平台.

        Args:
            message: 待发送的消息

        Returns:
            发送成功后的消息 ID
        """

    async def send_text(self, chat_id: str, text: str, reply_to: Optional[str] = None) -> str:
        """快捷方法 — 发送文本消息."""
        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            content=text,
            reply_to_id=reply_to,
        ))

    async def send_image(
        self,
        chat_id: str,
        image_url: str = "",
        image_data: bytes = b"",
        caption: str = "",
    ) -> str:
        """快捷方法 — 发送图片."""
        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            content=caption,
            message_type=MessageType.IMAGE,
            media_url=image_url,
            media_data=image_data,
        ))

    async def send_file(
        self,
        chat_id: str,
        file_url: str = "",
        file_data: bytes = b"",
        filename: str = "",
    ) -> str:
        """快捷方法 — 发送文件."""
        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            message_type=MessageType.FILE,
            media_url=file_url,
            media_data=file_data,
            metadata={"filename": filename},
        ))

    async def send_voice(
        self,
        chat_id: str,
        audio_data: bytes,
        fallback_text: str = "",
        audio_format: str = "mp3",
    ) -> str:
        """快捷方法 — 发送语音消息.

        如果平台不支持语音，自动降级为文本消息。

        Args:
            chat_id: 目标聊天 ID
            audio_data: 音频 bytes
            fallback_text: 语音不可用时的降级文本
            audio_format: 音频格式 ("mp3", "ogg", "opus", "wav")
        """
        if not self.capabilities.get("voice"):
            if fallback_text:
                return await self.send_text(chat_id, fallback_text)
            logger.warning("%s 不支持语音消息，且无降级文本", self.name)
            return ""

        return await self.send_message(OutgoingMessage(
            chat_id=chat_id,
            message_type=MessageType.VOICE,
            media_data=audio_data,
            content=fallback_text,
            metadata={"audio_format": audio_format},
        ))

    # ── 消息编辑/撤回 ────────────────────────────────────────

    async def edit_message(self, chat_id: str, message_id: str, new_content: str) -> bool:
        """编辑已发送的消息 (部分平台支持)."""
        logger.warning("%s does not support edit_message", self.name)
        return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """撤回/删除消息 (部分平台支持)."""
        logger.warning("%s does not support delete_message", self.name)
        return False

    # ── 事件处理器注册 ────────────────────────────────────────

    def on_message(self, handler: MessageHandler) -> None:
        """注册消息处理器."""
        self._message_handlers.append(handler)

    def on_event(self, handler: EventHandler) -> None:
        """注册事件处理器."""
        self._event_handlers.append(handler)

    # ── 内部方法 (子类调用) ─────────────────────────────────────

    async def _dispatch_message(self, message: PlatformMessage) -> None:
        """分发消息给所有处理器 (子类收到消息时调用)."""
        for handler in self._message_handlers:
            try:
                await handler(message)
            except Exception as e:
                logger.error(
                    "Message handler error (%s): %s", self.name, e, exc_info=True
                )

    async def _dispatch_event(self, event: PlatformEvent) -> None:
        """分发事件给所有处理器."""
        for handler in self._event_handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    "Event handler error (%s): %s", self.name, e, exc_info=True
                )

    # ── 平台能力查询 ──────────────────────────────────────────

    @property
    def capabilities(self) -> dict[str, bool]:
        """平台支持的能力."""
        return {
            "text": True,
            "image": False,
            "voice": False,
            "video": False,
            "file": False,
            "rich_text": False,
            "interactive": False,  # 交互式卡片
            "edit_message": False,
            "delete_message": False,
            "reaction": False,
            "thread": False,       # 话题/回复链
            "typing_indicator": False,
        }

    # ── 健康检查 ──────────────────────────────────────────────

    async def health_check(self) -> dict[str, Any]:
        """健康状态."""
        return {
            "platform": self.platform_type.value,
            "name": self.name,
            "running": self._running,
            "bot_user": self._bot_user.username if self._bot_user else None,
        }
