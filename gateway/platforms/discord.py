"""Discord 平台适配器 — discord.py.

支持:
- 私聊 + 群聊 (Guild channels)
- Slash commands
- 消息按钮/下拉菜单 (Components)
- 文件/图片附件
- 线程 (Thread)
- @bot 检测
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
    PlatformEvent,
    PlatformMessage,
    PlatformType,
    PlatformUser,
)

logger = logging.getLogger(__name__)

class DiscordAdapter(BasePlatformAdapter):
    """Discord 适配器."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.DISCORD, config)
        self._token = config.get("bot_token", "")
        self._prefix = config.get("command_prefix", "!")
        self._client = None
        self._bot_id: Optional[str] = None
        self._bot_name: str = ""

    @property
    def name(self) -> str:
        return "Discord"

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
            "slash_command": True,
        }

    async def start(self) -> None:
        try:
            import discord
            from discord.ext import commands
        except ImportError:
            raise ImportError("discord.py 未安装。请运行: pip install discord.py")

        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        self._client = commands.Bot(
            command_prefix=self._prefix,
            intents=intents,
        )

        @self._client.event
        async def on_ready():
            self._bot_id = str(self._client.user.id)
            self._bot_name = self._client.user.name
            logger.info("Discord bot ready: %s (%s)", self._bot_name, self._bot_id)

        @self._client.event
        async def on_message(message):
            if message.author.bot:
                return

            # 检查是否 @ 了 bot
            is_mentioned = self._client.user in message.mentions
            is_dm = isinstance(message.channel, discord.DMChannel)

            if not is_dm and not is_mentioned and not message.content.startswith(self._prefix):
                return

            content = message.content
            if is_mentioned:
                content = content.replace(f"<@{self._bot_id}>", "").replace(f"<@!{self._bot_id}>", "").strip()

            chat_type = ChatType.PRIVATE if is_dm else ChatType.GROUP

            platform_msg = PlatformMessage(
                platform=PlatformType.DISCORD,
                message_id=str(message.id),
                chat=PlatformChat(
                    chat_id=str(message.channel.id),
                    chat_type=chat_type,
                    title=getattr(message.channel, "name", "DM"),
                ),
                sender=PlatformUser(
                    user_id=str(message.author.id),
                    username=message.author.name,
                    display_name=message.author.display_name,
                ),
                message_type=MessageType.TEXT,
                content=content,
                raw=message,
            )

            # 处理附件
            if message.attachments:
                for att in message.attachments:
                    if att.content_type and att.content_type.startswith("image/"):
                        platform_msg.message_type = MessageType.IMAGE
                        platform_msg.media_url = att.url
                    else:
                        platform_msg.message_type = MessageType.FILE
                        platform_msg.media_url = att.url

            await self._dispatch_message(platform_msg)

        # 后台启动
        asyncio.create_task(self._client.start(self._token))
        logger.info("Discord adapter starting...")

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        if not self._client:
            return None

        try:
            import discord

            channel = self._client.get_channel(int(message.chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(message.chat_id))

            # 构建 embed (可选)
            embed = None
            if message.metadata.get("embed"):
                embed_data = message.metadata["embed"]
                embed = discord.Embed(
                    title=embed_data.get("title", ""),
                    description=embed_data.get("description", ""),
                    color=embed_data.get("color", 0x5865F2),
                )

            # 发送
            kwargs: dict[str, Any] = {}
            if message.reply_to_id:
                try:
                    ref_msg = await channel.fetch_message(int(message.reply_to_id))
                    kwargs["reference"] = ref_msg
                except Exception:
                    pass

            if embed:
                kwargs["embed"] = embed

            # Discord 限制 2000 字符
            text = message.content or ""
            if len(text) > 2000:
                chunks = [text[i:i+2000] for i in range(0, len(text), 2000)]
                sent = None
                for chunk in chunks:
                    sent = await channel.send(chunk, **kwargs)
                    kwargs.pop("reference", None)
                return str(sent.id) if sent else None
            else:
                sent = await channel.send(text, **kwargs)
                return str(sent.id)

        except Exception as e:
            logger.error("Discord send error: %s", e)
            return None

    async def send_text(self, chat_id: str, text: str) -> Optional[str]:
        return await self.send_message(OutgoingMessage(chat_id=chat_id, content=text))

    async def send_image(self, chat_id: str, image_url: str, caption: str = "") -> Optional[str]:
        if not self._client:
            return None
        try:
            import discord
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))

            embed = discord.Embed(description=caption) if caption else None
            if embed:
                embed.set_image(url=image_url)
                sent = await channel.send(embed=embed)
            else:
                sent = await channel.send(image_url)
            return str(sent.id)
        except Exception as e:
            logger.error("Discord send image error: %s", e)
            return None

    async def edit_message(self, chat_id: str, message_id: str, new_text: str) -> bool:
        if not self._client:
            return False
        try:
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.edit(content=new_text)
            return True
        except Exception as e:
            logger.error("Discord edit error: %s", e)
            return False

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        if not self._client:
            return False
        try:
            channel = self._client.get_channel(int(chat_id))
            if not channel:
                channel = await self._client.fetch_channel(int(chat_id))
            msg = await channel.fetch_message(int(message_id))
            await msg.delete()
            return True
        except Exception:
            return False

    async def health_check(self) -> bool:
        return self._client is not None and self._client.is_ready()
