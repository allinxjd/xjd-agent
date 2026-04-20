"""iMessage 平台适配器 — macOS only.

通过 AppleScript 发送消息，轮询 chat.db 接收消息。
仅支持 macOS。
"""

from __future__ import annotations

import asyncio
import logging
import platform
import sqlite3
import subprocess
import time
from pathlib import Path
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

class IMessageAdapter(BasePlatformAdapter):
    """iMessage 适配器 — macOS AppleScript + chat.db."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.IMESSAGE, config)
        self._poll_interval = config.get("poll_interval", 5)
        self._poll_task: Optional[asyncio.Task] = None
        self._last_rowid = 0
        self._db_path = Path.home() / "Library/Messages/chat.db"

    @property
    def name(self) -> str:
        return "iMessage"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": True, "file": True,
            "voice": False, "video": False,
            "edit_message": False, "delete_message": False,
        }

    async def start(self) -> None:
        if platform.system() != "Darwin":
            raise RuntimeError("iMessage adapter 仅支持 macOS")
        if not self._db_path.exists():
            raise RuntimeError(f"chat.db 不存在: {self._db_path}")

        # 获取当前最大 rowid
        conn = sqlite3.connect(str(self._db_path))
        cur = conn.execute("SELECT MAX(ROWID) FROM message")
        row = cur.fetchone()
        self._last_rowid = row[0] or 0
        conn.close()

        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("iMessage adapter started")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()

    async def _poll_loop(self) -> None:
        while self._running:
            conn = None
            try:
                conn = sqlite3.connect(str(self._db_path))
                cur = conn.execute("""
                    SELECT m.ROWID, m.text, m.is_from_me,
                           h.id AS sender, m.date
                    FROM message m
                    LEFT JOIN handle h ON m.handle_id = h.ROWID
                    WHERE m.ROWID > ? AND m.is_from_me = 0
                    ORDER BY m.ROWID
                    LIMIT 20
                """, (self._last_rowid,))

                for row in cur.fetchall():
                    rowid, text, is_from_me, sender, date = row
                    self._last_rowid = rowid
                    if not text or not sender:
                        continue

                    msg = PlatformMessage(
                        message_id=str(rowid),
                        platform=PlatformType.IMESSAGE,
                        chat=PlatformChat(
                            chat_id=sender,
                            chat_type=ChatType.PRIVATE,
                        ),
                        sender=PlatformUser(user_id=sender, username=sender),
                        message_type=MessageType.TEXT,
                        content=text,
                    )
                    await self._dispatch_message(msg)

                conn.close()
            except Exception as e:
                logger.error("iMessage poll error: %s", e)
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

            await asyncio.sleep(self._poll_interval)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            recipient = message.chat_id
            text = message.content.replace('"', '\\"')
            script = f'''
            tell application "Messages"
                set targetService to 1st account whose service type = iMessage
                set targetBuddy to participant "{recipient}" of targetService
                send "{text}" to targetBuddy
            end tell
            '''
            proc = await asyncio.create_subprocess_exec(
                "osascript", "-e", script,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.wait()
            return f"sent-{int(time.time())}"
        except Exception as e:
            logger.error("iMessage send error: %s", e)
            return None
