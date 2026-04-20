"""Telegram 适配器 — 基于 python-telegram-bot.

功能:
- 私聊 & 群聊消息接收
- 文本/图片/语音/文件/位置消息处理
- Inline 键盘交互
- Webhook / Polling 两种模式
- /start /help 命令自动响应
- 消息编辑/删除支持

依赖: pip install "xjd-agent[telegram]"
"""

from __future__ import annotations

import asyncio
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

class TelegramAdapter(BasePlatformAdapter):
    """Telegram Bot 适配器.

    配置 (config dict):
        bot_token: str          # Bot token from @BotFather
        webhook_url: str        # (可选) Webhook URL, 不设则用 polling
        webhook_port: int       # (可选) Webhook 端口, 默认 8443
        allowed_updates: list   # (可选) 订阅的更新类型
        parse_mode: str         # (可选) 默认消息格式 "Markdown" | "HTML"

    用法:
        adapter = TelegramAdapter({
            "bot_token": "123:ABC...",
        })
        adapter.on_message(my_handler)
        await adapter.start()
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.TELEGRAM, config)
        self._bot_token = config.get("bot_token", "")
        self._webhook_url = config.get("webhook_url", "")
        self._webhook_port = config.get("webhook_port", 8443)
        self._parse_mode = config.get("parse_mode", "Markdown")
        self._app = None  # telegram.ext.Application instance
        self._polling_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "Telegram"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "voice": True,
            "video": True,
            "file": True,
            "rich_text": True,   # Markdown / HTML
            "interactive": True,  # Inline keyboard
            "edit_message": True,
            "delete_message": True,
            "reaction": True,
            "thread": True,
            "typing_indicator": True,
        }

    async def start(self) -> None:
        """启动 Telegram Bot."""
        try:
            from telegram import Update
            from telegram.ext import (
                Application,
                CommandHandler,
                MessageHandler,
                filters,
            )
        except ImportError:
            raise ImportError(
                'Telegram 依赖未安装。请运行: pip install "xjd-agent[telegram]"'
            )

        if not self._bot_token:
            raise ValueError("Telegram bot_token 未配置")

        # 构建 Application
        builder = Application.builder().token(self._bot_token)
        self._app = builder.build()

        # 注册处理器
        self._app.add_handler(CommandHandler("start", self._handle_start))
        self._app.add_handler(CommandHandler("help", self._handle_help))
        self._app.add_handler(
            MessageHandler(filters.ALL & ~filters.COMMAND, self._handle_message)
        )

        # 获取 Bot 信息
        await self._app.initialize()
        bot_info = await self._app.bot.get_me()
        self._bot_user = PlatformUser(
            user_id=str(bot_info.id),
            username=bot_info.username or "",
            display_name=bot_info.first_name or "",
            is_bot=True,
        )

        # 启动
        if self._webhook_url:
            # Webhook 模式
            await self._app.bot.set_webhook(
                url=self._webhook_url,
                allowed_updates=["message", "edited_message", "callback_query"],
            )
            logger.info("Telegram webhook set: %s", self._webhook_url)
            await self._app.start()
        else:
            # Polling 模式
            await self._app.start()
            self._polling_task = asyncio.create_task(
                self._app.updater.start_polling(  # type: ignore
                    allowed_updates=[
                        "message", "edited_message", "callback_query",
                    ],
                    drop_pending_updates=True,
                )
            )
            logger.info("Telegram polling started")

        self._running = True
        logger.info("Telegram adapter started: @%s", self._bot_user.username)

    async def stop(self) -> None:
        """停止 Telegram Bot."""
        self._running = False

        if self._app:
            if self._polling_task:
                if self._app.updater and self._app.updater.running:
                    await self._app.updater.stop()
                self._polling_task = None
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

        logger.info("Telegram adapter stopped")

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息到 Telegram."""
        if not self._app:
            raise RuntimeError("Telegram adapter not started")

        bot = self._app.bot
        chat_id = message.chat_id

        try:
            if message.message_type == MessageType.IMAGE:
                if message.media_url:
                    result = await bot.send_photo(
                        chat_id=chat_id,
                        photo=message.media_url,
                        caption=message.content or None,
                        parse_mode=self._parse_mode,
                        reply_to_message_id=int(message.reply_to_id) if message.reply_to_id else None,
                    )
                elif message.media_data:
                    result = await bot.send_photo(
                        chat_id=chat_id,
                        photo=message.media_data,
                        caption=message.content or None,
                        parse_mode=self._parse_mode,
                    )
                else:
                    raise ValueError("Image message requires media_url or media_data")
            elif message.message_type == MessageType.FILE:
                result = await bot.send_document(
                    chat_id=chat_id,
                    document=message.media_url or message.media_data,
                    filename=message.metadata.get("filename"),
                    caption=message.content or None,
                )
            elif message.message_type == MessageType.VOICE:
                result = await bot.send_voice(
                    chat_id=chat_id,
                    voice=message.media_url or message.media_data,
                )
            else:
                # 文本消息
                # 分割过长消息 (Telegram 限制 4096 字符)
                text = message.content
                if len(text) > 4096:
                    parts = [text[i:i + 4096] for i in range(0, len(text), 4096)]
                    result = None
                    for part in parts:
                        result = await bot.send_message(
                            chat_id=chat_id,
                            text=part,
                            parse_mode=self._parse_mode,
                            reply_to_message_id=(
                                int(message.reply_to_id) if message.reply_to_id and result is None else None
                            ),
                        )
                    return str(result.message_id) if result else ""
                else:
                    result = await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode=self._parse_mode,
                        reply_to_message_id=int(message.reply_to_id) if message.reply_to_id else None,
                    )

            return str(result.message_id)

        except Exception as e:
            logger.error("Telegram send_message failed: %s", e, exc_info=True)
            # 如果 Markdown 解析失败，降级为纯文本
            if "parse" in str(e).lower():
                try:
                    result = await bot.send_message(
                        chat_id=chat_id,
                        text=message.content,
                    )
                    return str(result.message_id)
                except Exception:
                    pass
            raise

    async def edit_message(self, chat_id: str, message_id: str, new_content: str) -> bool:
        """编辑已发送的消息."""
        if not self._app:
            return False
        try:
            await self._app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(message_id),
                text=new_content,
                parse_mode=self._parse_mode,
            )
            return True
        except Exception as e:
            logger.error("Telegram edit_message failed: %s", e)
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """删除消息."""
        if not self._app:
            return False
        try:
            await self._app.bot.delete_message(
                chat_id=chat_id,
                message_id=int(message_id),
            )
            return True
        except Exception as e:
            logger.error("Telegram delete_message failed: %s", e)
            return False

    # ── 内部处理器 ────────────────────────────────────────────

    async def _handle_start(self, update: Any, context: Any) -> None:
        """处理 /start 命令."""
        if not update.message:
            return
        await update.message.reply_text(
            "你好！我是 XJD 小巨蛋智能体。\n"
            "直接发消息给我就可以对话。\n"
            "输入 /help 查看更多功能。"
        )

    async def _handle_help(self, update: Any, context: Any) -> None:
        """处理 /help 命令."""
        if not update.message:
            return
        await update.message.reply_text(
            "📖 *小巨蛋智能体功能*\n\n"
            "• 直接发送文字进行对话\n"
            "• 发送图片进行视觉分析\n"
            "• 发送语音自动转文字\n"
            "• 发送文件进行分析\n\n"
            "🔧 *工具能力*\n"
            "• 执行代码和终端命令\n"
            "• 搜索网页获取最新信息\n"
            "• 读写和编辑文件\n"
            "• 定时任务自动化\n",
            parse_mode="Markdown",
        )

    async def _handle_message(self, update: Any, context: Any) -> None:
        """处理所有消息."""
        if not update.message:
            return

        msg = update.message

        # 确定聊天类型
        if msg.chat.type == "private":
            chat_type = ChatType.PRIVATE
        elif msg.chat.type in ("group", "supergroup"):
            chat_type = ChatType.GROUP
        else:
            chat_type = ChatType.CHANNEL

        # 构建统一消息
        sender = PlatformUser(
            user_id=str(msg.from_user.id),
            username=msg.from_user.username or "",
            display_name=msg.from_user.first_name or "",
        )

        chat = PlatformChat(
            chat_id=str(msg.chat.id),
            chat_type=chat_type,
            title=msg.chat.title or "",
            platform=PlatformType.TELEGRAM,
        )

        # 检测消息类型
        message_type = MessageType.TEXT
        content = msg.text or msg.caption or ""
        media_url = ""

        if msg.photo:
            message_type = MessageType.IMAGE
            # 取最高质量的图片
            photo = msg.photo[-1]
            file = await photo.get_file()
            media_url = file.file_path or ""
        elif msg.voice or msg.audio:
            message_type = MessageType.VOICE
            voice = msg.voice or msg.audio
            file = await voice.get_file()
            media_url = file.file_path or ""
        elif msg.video:
            message_type = MessageType.VIDEO
            file = await msg.video.get_file()
            media_url = file.file_path or ""
        elif msg.document:
            message_type = MessageType.FILE
            file = await msg.document.get_file()
            media_url = file.file_path or ""
        elif msg.location:
            message_type = MessageType.LOCATION
            content = f"位置: {msg.location.latitude}, {msg.location.longitude}"

        # 检测 @提及
        mentions = []
        if msg.entities:
            for entity in msg.entities:
                if entity.type == "mention" and msg.text:
                    mentions.append(msg.text[entity.offset:entity.offset + entity.length])

        platform_msg = PlatformMessage(
            message_id=str(msg.message_id),
            platform=PlatformType.TELEGRAM,
            chat=chat,
            sender=sender,
            message_type=message_type,
            content=content,
            media_url=media_url,
            reply_to_id=str(msg.reply_to_message.message_id) if msg.reply_to_message else None,
            mentions=mentions,
            timestamp=time.time(),
            raw=msg,
        )

        # 在群聊中，只响应 @bot 的消息或回复 bot 的消息
        if chat_type == ChatType.GROUP:
            bot_mentioned = False
            if self._bot_user:
                bot_username = f"@{self._bot_user.username}"
                if bot_username.lower() in content.lower():
                    bot_mentioned = True
                    # 去掉 @bot
                    content = content.replace(bot_username, "").strip()
                    platform_msg.content = content

            if msg.reply_to_message and self._bot_user:
                if str(msg.reply_to_message.from_user.id) == self._bot_user.user_id:
                    bot_mentioned = True

            if not bot_mentioned:
                return  # 群聊中未提及 bot，忽略

        # 发送 typing indicator
        try:
            await msg.chat.send_action("typing")
        except Exception:
            pass

        # 分发消息
        await self._dispatch_message(platform_msg)

    async def health_check(self) -> dict[str, Any]:
        """健康检查."""
        result = await super().health_check()
        if self._app:
            try:
                bot_info = await self._app.bot.get_me()
                result["bot_id"] = bot_info.id
                result["bot_username"] = bot_info.username
                result["healthy"] = True
            except Exception as e:
                result["healthy"] = False
                result["error"] = str(e)
        return result
