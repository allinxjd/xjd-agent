"""IRC 平台适配器.

支持:
- 多频道加入
- 私聊 + 频道消息
- SSL/TLS
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

class IRCAdapter(BasePlatformAdapter):
    """IRC 适配器."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.IRC, config)
        self._server = config.get("server", "irc.libera.chat")
        self._port = config.get("port", 6697)
        self._nickname = config.get("nickname", "xjd-bot")
        self._channels = config.get("channels", [])
        self._password = config.get("password", "")
        self._use_ssl = config.get("use_ssl", True)
        self._reader = None
        self._writer = None
        self._recv_task: Optional[asyncio.Task] = None

    @property
    def name(self) -> str:
        return "IRC"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": False, "file": False,
            "voice": False, "video": False,
            "edit_message": False, "delete_message": False,
        }

    async def start(self) -> None:
        if self._use_ssl:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            self._reader, self._writer = await asyncio.open_connection(
                self._server, self._port, ssl=ctx,
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self._server, self._port,
            )

        if self._password:
            self._send_raw(f"PASS {self._password}")
        self._send_raw(f"NICK {self._nickname}")
        self._send_raw(f"USER {self._nickname} 0 * :XJD Agent Bot")

        self._running = True
        self._recv_task = asyncio.create_task(self._recv_loop())
        logger.info("IRC adapter connecting to %s:%d", self._server, self._port)

    async def stop(self) -> None:
        self._running = False
        if self._writer:
            self._send_raw("QUIT :Bye")
            self._writer.close()
        if self._recv_task:
            self._recv_task.cancel()

    def _send_raw(self, line: str) -> None:
        if self._writer:
            self._writer.write((line + "\r\n").encode("utf-8"))

    async def _recv_loop(self) -> None:
        while self._running and self._reader:
            try:
                line = await self._reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()

                if text.startswith("PING"):
                    self._send_raw(text.replace("PING", "PONG", 1))
                    continue

                # 001 = welcome, join channels
                if " 001 " in text:
                    for ch in self._channels:
                        self._send_raw(f"JOIN {ch}")

                # PRIVMSG
                if "PRIVMSG" in text:
                    await self._handle_privmsg(text)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("IRC recv error: %s", e)

    async def _handle_privmsg(self, raw: str) -> None:
        try:
            prefix, _, rest = raw.partition(" PRIVMSG ")
            target, _, content = rest.partition(" :")
            sender = prefix.lstrip(":").split("!")[0]

            chat_type = ChatType.PRIVATE if target == self._nickname else ChatType.GROUP
            chat_id = sender if chat_type == ChatType.PRIVATE else target

            msg = PlatformMessage(
                message_id=str(int(time.time() * 1000)),
                platform=PlatformType.IRC,
                chat=PlatformChat(chat_id=chat_id, chat_type=chat_type, title=chat_id),
                sender=PlatformUser(user_id=sender, username=sender),
                message_type=MessageType.TEXT,
                content=content,
                raw=raw,
            )
            await self._dispatch_message(msg)
        except Exception as e:
            logger.error("IRC parse error: %s", e)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        if not self._writer:
            return None
        # IRC 限制 512 字节/行
        for line in message.content.split("\n"):
            if line.strip():
                self._send_raw(f"PRIVMSG {message.chat_id} :{line[:400]}")
        return f"sent-{int(time.time())}"
