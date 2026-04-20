"""Email 平台适配器 — aiosmtplib + IMAP.

支持:
- SMTP 发送邮件
- IMAP IDLE 接收邮件
- HTML 富文本
- 附件
"""

from __future__ import annotations

import asyncio
import email
import email.mime.text
import email.mime.multipart
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

class EmailAdapter(BasePlatformAdapter):
    """Email 适配器 — SMTP 发送 + IMAP 接收."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.EMAIL, config)
        self._smtp_host = config.get("smtp_host", "smtp.gmail.com")
        self._smtp_port = config.get("smtp_port", 587)
        self._imap_host = config.get("imap_host", "imap.gmail.com")
        self._imap_port = config.get("imap_port", 993)
        self._username = config.get("username", "")
        self._password = config.get("password", "")
        self._use_tls = config.get("use_tls", True)
        self._poll_interval = config.get("poll_interval", 30)
        self._poll_task: Optional[asyncio.Task] = None
        self._last_uid = 0

    @property
    def name(self) -> str:
        return "Email"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": False,
            "file": True,
            "voice": False,
            "video": False,
            "rich_text": True,
            "edit_message": False,
            "delete_message": False,
            "reply": True,
            "thread": True,
        }

    async def start(self) -> None:
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("Email adapter started (%s)", self._username)

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll_loop(self) -> None:
        """轮询 IMAP 收件箱."""
        try:
            import imaplib
        except ImportError:
            logger.error("imaplib not available")
            return

        while self._running:
            try:
                imap = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
                imap.login(self._username, self._password)
                imap.select("INBOX")

                criteria = f"(UNSEEN)"
                _, data = imap.search(None, criteria)
                msg_ids = data[0].split()

                for mid in msg_ids[-10:]:  # 最多处理 10 封
                    _, msg_data = imap.fetch(mid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    sender_addr = email.utils.parseaddr(msg["From"])[1]
                    subject = msg.get("Subject", "")

                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode("utf-8", errors="replace")

                    platform_msg = PlatformMessage(
                        message_id=mid.decode(),
                        platform=PlatformType.EMAIL,
                        chat=PlatformChat(
                            chat_id=sender_addr,
                            chat_type=ChatType.PRIVATE,
                            title=subject,
                        ),
                        sender=PlatformUser(
                            user_id=sender_addr,
                            username=sender_addr,
                        ),
                        message_type=MessageType.TEXT,
                        content=f"[{subject}]\n{body}",
                        raw=msg,
                    )
                    await self._dispatch_message(platform_msg)

                imap.logout()
            except Exception as e:
                logger.error("Email poll error: %s", e)

            await asyncio.sleep(self._poll_interval)

    async def send_message(self, message: OutgoingMessage) -> Optional[str]:
        try:
            import aiosmtplib
        except ImportError:
            logger.error("aiosmtplib 未安装。请运行: pip install aiosmtplib")
            return None

        try:
            msg = email.mime.multipart.MIMEMultipart()
            msg["From"] = self._username
            msg["To"] = message.chat_id
            msg["Subject"] = message.metadata.get("subject", "XJD Agent")

            body = email.mime.text.MIMEText(message.content, "plain", "utf-8")
            msg.attach(body)

            await aiosmtplib.send(
                msg,
                hostname=self._smtp_host,
                port=self._smtp_port,
                username=self._username,
                password=self._password,
                use_tls=self._use_tls,
            )
            return f"sent-{int(time.time())}"
        except Exception as e:
            logger.error("Email send error: %s", e)
            return None

    async def health_check(self) -> dict[str, Any]:
        return {
            "platform": "email",
            "name": self.name,
            "running": self._running,
            "username": self._username,
        }
