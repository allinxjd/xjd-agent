"""微信 iLink 协议适配器.

通过 wechat-clawbot SDK 接入个人微信:
- 认证: QR 码扫描登录 → 获取 bot_token (非阻塞，后台等待)
- 消息收取: long-poll (getupdates) + 媒体 CDN 解密下载
- 消息发送: 文本 / 图片 / 文件 / 视频 / 语音 (CDN 上传)
- 主动发消息: 给已知联系人发送消息 (需对方先发过消息)

依赖: pip install "xjd-agent[wechat-clawbot]"
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
import uuid
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

_ILINK_BASE = "https://ilinkai.weixin.qq.com"
_CDN_BASE = "https://novac2c.cdn.weixin.qq.com/c2c"
_SESSION_EXPIRED_ERRCODE = -14


class WeChatClawBotAdapter(BasePlatformAdapter):
    """微信 iLink 适配器 — QR 扫码登录 + long-poll 收发消息 + 全媒体支持."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.WECHAT_CLAWBOT, config)
        self._account_id: str = config.get("account_id", "")
        self._bot_token: str = ""
        self._base_url: str = _ILINK_BASE
        self._cdn_base_url: str = config.get("cdn_base_url", _CDN_BASE)
        self._poll_task: Optional[asyncio.Task] = None
        self._login_task: Optional[asyncio.Task] = None
        self._get_updates_buf: str = ""
        self._running: bool = False
        self._qr_url: str = ""
        self._login_status: str = "idle"

    @property
    def name(self) -> str:
        return "微信个人号"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": True, "voice": True, "video": True,
            "file": True, "rich_text": False, "interactive": False,
            "edit_message": False, "delete_message": False,
            "reaction": False, "thread": False, "typing_indicator": True,
        }

    @property
    def login_state(self) -> dict[str, Any]:
        """供 WebUI 查询的登录状态."""
        return {
            "status": self._login_status,
            "qr_url": self._qr_url,
            "account_id": self._account_id,
        }

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        try:
            from wechat_clawbot.auth.accounts import (
                list_weixin_account_ids,
                load_weixin_account,
            )
            from wechat_clawbot.messaging.inbound import restore_context_tokens
        except ImportError:
            raise ImportError(
                'wechat-clawbot 未安装。请运行: pip install "xjd-agent[wechat-clawbot]"'
            )

        self._running = True

        if not self._account_id:
            ids = list_weixin_account_ids()
            if ids:
                self._account_id = ids[0]

        # 恢复 get_updates_buf 游标
        self._restore_sync_buf()

        if self._account_id:
            data = load_weixin_account(self._account_id)
            if data and data.token:
                self._bot_token = data.token
                self._base_url = data.base_url or _ILINK_BASE
                restore_context_tokens(self._account_id)
                self._login_status = "connected"
                logger.info("已恢复微信 token (account=%s)", self._account_id)
                self._begin_poll()
                return

        self._login_status = "waiting_scan"
        self._login_task = asyncio.create_task(self._qr_login_flow())
        logger.info("微信适配器已启动，等待扫码登录...")

    def _restore_sync_buf(self) -> None:
        if not self._account_id:
            return
        try:
            from wechat_clawbot.storage.sync_buf import (
                get_sync_buf_file_path,
                load_get_updates_buf,
            )
            path = get_sync_buf_file_path(self._account_id)
            buf = load_get_updates_buf(path)
            if buf:
                self._get_updates_buf = buf
                logger.info("已恢复 get_updates_buf (account=%s)", self._account_id)
        except Exception as e:
            logger.warning("恢复 sync_buf 失败: %s", e)

    def _persist_sync_buf(self) -> None:
        if not self._account_id or not self._get_updates_buf:
            return
        try:
            from wechat_clawbot.storage.sync_buf import (
                get_sync_buf_file_path,
                save_get_updates_buf,
            )
            path = get_sync_buf_file_path(self._account_id)
            save_get_updates_buf(path, self._get_updates_buf)
        except Exception as e:
            logger.debug("持久化 sync_buf 失败: %s", e)

    async def _qr_login_flow(self) -> None:
        from wechat_clawbot.auth.accounts import (
            register_weixin_account_id,
            save_weixin_account,
        )
        from wechat_clawbot.auth.login_qr import (
            start_weixin_login_with_qr,
            wait_for_weixin_login,
        )

        try:
            qr = await start_weixin_login_with_qr(api_base_url=_ILINK_BASE)
            self._qr_url = qr.qrcode_url or ""
            if not self._qr_url:
                self._login_status = "error"
                logger.error("获取二维码失败: %s", qr.message)
                return
            logger.info("请用微信扫描二维码: %s", self._qr_url)

            result = await wait_for_weixin_login(
                session_key=qr.session_key,
                api_base_url=_ILINK_BASE,
                verbose=True,
            )
            if not result.connected or not result.bot_token:
                self._login_status = "error"
                logger.error("微信登录失败: %s", result.message)
                return

            self._bot_token = result.bot_token
            self._base_url = result.base_url or _ILINK_BASE
            self._account_id = result.account_id or ""
            self._qr_url = ""
            self._login_status = "connected"

            if self._account_id:
                save_weixin_account(
                    self._account_id,
                    token=self._bot_token,
                    base_url=self._base_url,
                    user_id=result.user_id,
                )
                register_weixin_account_id(self._account_id)
                self._restore_sync_buf()

            logger.info("微信登录成功! account=%s", self._account_id)
            self._begin_poll()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self._login_status = "error"
            logger.error("QR 登录流程异常: %s", e, exc_info=True)

    def _begin_poll(self) -> None:
        self._bot_user = PlatformUser(
            user_id=self._account_id or "clawbot",
            username="iLink Bot",
            display_name="XJD Agent",
            is_bot=True,
        )
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        self._running = False
        for task in (self._login_task, self._poll_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._login_task = self._poll_task = None
        self._login_status = "idle"

        # 持久化游标 + 关闭 SDK HTTP clients
        self._persist_sync_buf()
        try:
            from wechat_clawbot.api.client import close_shared_client
            from wechat_clawbot.cdn.download import close_cdn_dl_client
            from wechat_clawbot.cdn.upload import close_cdn_ul_client
            await close_shared_client()
            await close_cdn_dl_client()
            await close_cdn_ul_client()
        except Exception as e:
            logger.debug("关闭 SDK clients: %s", e)

    # ── 消息轮询 ──────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        from wechat_clawbot.api.client import get_updates
        from wechat_clawbot.api.session_guard import pause_session

        retry_delay = 1.0
        while self._running:
            try:
                resp = await get_updates(
                    base_url=self._base_url,
                    token=self._bot_token,
                    get_updates_buf=self._get_updates_buf,
                )
                retry_delay = 1.0

                # session expired → 暂停避免无效重试
                if resp.errcode == _SESSION_EXPIRED_ERRCODE:
                    logger.warning("iLink session expired, pausing...")
                    if self._account_id:
                        pause_session(self._account_id)
                    self._login_status = "error"
                    await asyncio.sleep(60)
                    continue

                if resp.get_updates_buf:
                    self._get_updates_buf = resp.get_updates_buf
                    self._persist_sync_buf()

                for msg in resp.msgs or []:
                    await self._handle_incoming(msg)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("iLink poll 失败: %s, %.0fs 后重试", e, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30.0)

    async def _handle_incoming(self, msg: Any) -> None:
        from wechat_clawbot.api.types import MessageItemType
        from wechat_clawbot.cdn.download import (
            download_and_decrypt_buffer,
            download_plain_cdn_buffer,
        )
        from wechat_clawbot.media.silk import silk_to_wav
        from wechat_clawbot.messaging.inbound import (
            body_from_item_list,
            set_context_token,
        )

        try:
            sender_id = msg.from_user_id or ""
            if not sender_id:
                return
            if msg.context_token and self._account_id:
                set_context_token(self._account_id, sender_id, msg.context_token)

            items = msg.item_list or []
            content = body_from_item_list(items)
            msg_type = MessageType.TEXT
            media_data = b""

            for item in items:
                if item.type == MessageItemType.IMAGE:
                    msg_type = MessageType.IMAGE
                    media_data = await self._download_media_item(
                        item.image_item, download_and_decrypt_buffer,
                        download_plain_cdn_buffer, "image",
                    )
                elif item.type == MessageItemType.VOICE:
                    msg_type = MessageType.VOICE
                    raw = await self._download_media_item(
                        item.voice_item, download_and_decrypt_buffer,
                        download_plain_cdn_buffer, "voice",
                    )
                    if raw:
                        wav = await silk_to_wav(raw)
                        media_data = wav if wav else raw
                elif item.type == MessageItemType.FILE:
                    msg_type = MessageType.FILE
                    media_data = await self._download_media_item(
                        item.file_item, download_and_decrypt_buffer,
                        download_plain_cdn_buffer, "file",
                    )
                elif item.type == MessageItemType.VIDEO:
                    msg_type = MessageType.VIDEO
                    media_data = await self._download_media_item(
                        item.video_item, download_and_decrypt_buffer,
                        download_plain_cdn_buffer, "video",
                    )

            if not content and not media_data:
                return

            sender = PlatformUser(user_id=sender_id, username=sender_id)
            chat = PlatformChat(
                chat_id=sender_id, chat_type=ChatType.PRIVATE,
                platform=PlatformType.WECHAT_CLAWBOT,
            )
            platform_msg = PlatformMessage(
                message_id=str(msg.message_id or uuid.uuid4().hex[:16]),
                platform=PlatformType.WECHAT_CLAWBOT,
                chat=chat, sender=sender,
                message_type=msg_type, content=content.strip(),
                media_data=media_data, timestamp=time.time(), raw=msg,
            )
            await self._dispatch_message(platform_msg)
        except Exception as e:
            logger.error("消息处理失败: %s", e, exc_info=True)

    async def _download_media_item(
        self, media_holder: Any, decrypt_fn: Any, plain_fn: Any, label: str,
    ) -> bytes:
        """从 CDN 下载并解密媒体 item."""
        if not media_holder or not getattr(media_holder, "media", None):
            return b""
        media = media_holder.media
        if not getattr(media, "has_download_source", False):
            return b""
        try:
            import base64
            aes_key = getattr(media_holder, "aeskey", None) or getattr(media, "aes_key", None)
            if aes_key and not aes_key.startswith("base64:"):
                if len(aes_key) % 2 == 0 and all(c in "0123456789abcdefABCDEF" for c in aes_key):
                    aes_key = base64.b64encode(bytes.fromhex(aes_key)).decode()
            if aes_key:
                return await decrypt_fn(
                    media.encrypt_query_param or "", aes_key,
                    self._cdn_base_url, label, full_url=media.full_url,
                )
            return await plain_fn(
                media.encrypt_query_param or "", self._cdn_base_url,
                label, full_url=media.full_url,
            )
        except Exception as e:
            logger.warning("媒体下载失败 (%s): %s", label, e)
            return b""

    # ── 发送消息 ──────────────────────────────────────────────

    async def send_typing(self, chat_id: str, action: str = "start") -> None:
        if not self._bot_token:
            return
        from wechat_clawbot.api.client import WeixinApiOptions, get_config, send_typing
        from wechat_clawbot.api.types import SendTypingReq, TypingStatus

        opts = WeixinApiOptions(base_url=self._base_url, token=self._bot_token)
        try:
            cfg = await get_config(opts, ilink_user_id=chat_id)
            if not cfg.typing_ticket:
                return
            status = TypingStatus.TYPING if action == "start" else TypingStatus.CANCEL
            await send_typing(opts, SendTypingReq(
                ilink_user_id=chat_id,
                typing_ticket=cfg.typing_ticket,
                status=int(status),
            ))
        except Exception as e:
            logger.debug("send_typing 失败: %s", e)

    def _make_opts(self, to_user: str) -> Any:
        from wechat_clawbot.api.client import WeixinApiOptions
        from wechat_clawbot.messaging.inbound import get_context_token
        ctx_token = get_context_token(self._account_id, to_user) if self._account_id else None
        return WeixinApiOptions(
            base_url=self._base_url,
            token=self._bot_token,
            context_token=ctx_token,
        )

    async def send_message(self, message: OutgoingMessage) -> str:
        from wechat_clawbot.messaging.send import (
            markdown_to_plain_text,
            send_message_weixin,
        )
        from wechat_clawbot.messaging.send_media import send_weixin_media_file

        if not self._bot_token:
            logger.warning("尚未登录，无法发送消息")
            return ""

        to_user = message.chat_id
        opts = self._make_opts(to_user)

        try:
            if message.message_type == MessageType.TEXT:
                text = markdown_to_plain_text(message.content or "")
                result = await send_message_weixin(to_user, text, opts)
                return result.get("messageId", "")

            # 媒体消息: 写入临时文件 → send_weixin_media_file 自动路由
            file_path = await self._prepare_media_file(message)
            if not file_path:
                if message.content:
                    text = markdown_to_plain_text(message.content)
                    result = await send_message_weixin(to_user, text, opts)
                    return result.get("messageId", "")
                return ""

            caption = markdown_to_plain_text(message.content) if message.content else ""
            upload_opts = self._make_opts(to_user)
            upload_opts.context_token = None
            result = await send_weixin_media_file(
                file_path, to_user, caption, opts, self._cdn_base_url,
            )
            # 清理临时文件
            try:
                os.unlink(file_path)
            except OSError:
                pass
            return result.get("messageId", "")
        except Exception as e:
            logger.error("发送消息失败: %s", e)
            return ""

    async def _prepare_media_file(self, message: OutgoingMessage) -> str:
        """将 media_url 或 media_data 写入临时文件，返回路径."""
        ext_map = {
            MessageType.IMAGE: ".png",
            MessageType.VOICE: ".mp3",
            MessageType.VIDEO: ".mp4",
            MessageType.FILE: "",
        }
        ext = ext_map.get(message.message_type, "")
        filename = message.metadata.get("filename", "")
        if filename:
            _, fext = os.path.splitext(filename)
            ext = fext or ext

        if message.media_data:
            fd, path = tempfile.mkstemp(suffix=ext, prefix="wxbot_")
            os.write(fd, message.media_data)
            os.close(fd)
            return path

        if message.media_url:
            try:
                from wechat_clawbot.cdn.upload import download_remote_image_to_temp
                return await download_remote_image_to_temp(
                    message.media_url, tempfile.gettempdir(),
                )
            except Exception as e:
                logger.warning("下载远程媒体失败: %s", e)
                return ""

        return ""

    # ── 主动发消息 + 联系人 ────────────────────────────────────

    def list_known_contacts(self) -> list[str]:
        """返回所有已知联系人 user_id (曾发过消息的用户)."""
        if not self._account_id:
            return []
        try:
            from wechat_clawbot.messaging.inbound import _context_token_store
            prefix = f"{self._account_id}:"
            return [
                k[len(prefix):] for k in _context_token_store
                if k.startswith(prefix)
            ]
        except Exception:
            return []

    async def send_to_contact(self, user_id: str, text: str) -> str:
        """主动给已知联系人发送文本消息."""
        if not self._bot_token:
            return ""
        from wechat_clawbot.messaging.send import (
            markdown_to_plain_text,
            send_message_weixin,
        )
        opts = self._make_opts(user_id)
        try:
            plain = markdown_to_plain_text(text)
            result = await send_message_weixin(user_id, plain, opts)
            return result.get("messageId", "")
        except Exception as e:
            logger.error("主动发消息失败 (to=%s): %r", user_id, e, exc_info=True)
            return ""

