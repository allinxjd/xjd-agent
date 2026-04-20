"""微信 (WeChat) 适配器.

功能:
- 企业微信自建应用消息收发 (AES 加解密)
- 个人微信 (Wechaty)
- 文本/图片/语音消息处理
- 群聊 @机器人 触发

依赖: pip install "xjd-agent[wechat]"  (cryptography, httpx)
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import socket
import string
import struct
import time
import xml.etree.ElementTree as ET
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


# ── 企业微信消息加解密 (WXBizMsgCrypt) ──────────────────────────

class WXBizMsgCrypt:
    """企业微信消息加解密.

    实现: AES-256-CBC, PKCS#7 padding, base64 编码.
    协议: https://developer.work.weixin.qq.com/document/10514
    """

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str) -> None:
        self._token = token
        self._corp_id = corp_id
        # EncodingAESKey 是 base64 编码的 (43字符 + 补 '=' = 44字符)
        self._aes_key = base64.b64decode(encoding_aes_key + "=")
        self._iv = self._aes_key[:16]

    def _sign(self, timestamp: str, nonce: str, encrypt: str) -> str:
        """生成签名: SHA1(sort([token, timestamp, nonce, encrypt]))."""
        items = sorted([self._token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()

    def _encrypt(self, plaintext: str) -> str:
        """加密明文 → base64 密文."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        # random(16) + msg_len(4, network order) + msg + corp_id
        msg_bytes = plaintext.encode("utf-8")
        corp_bytes = self._corp_id.encode("utf-8")
        rand_bytes = os.urandom(16)
        body = rand_bytes + struct.pack("!I", len(msg_bytes)) + msg_bytes + corp_bytes
        # PKCS#7 padding to block_size=32
        pad_len = 32 - (len(body) % 32)
        body += bytes([pad_len] * pad_len)
        cipher = Cipher(algorithms.AES(self._aes_key), modes.CBC(self._iv))
        enc = cipher.encryptor()
        encrypted = enc.update(body) + enc.finalize()
        return base64.b64encode(encrypted).decode("utf-8")

    def _decrypt(self, ciphertext: str) -> str:
        """base64 密文 → 解密明文."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        encrypted = base64.b64decode(ciphertext)
        cipher = Cipher(algorithms.AES(self._aes_key), modes.CBC(self._iv))
        dec = cipher.decryptor()
        decrypted = dec.update(encrypted) + dec.finalize()
        # 去 PKCS#7 padding
        pad_len = decrypted[-1]
        content = decrypted[:-pad_len]
        # 跳过 random(16), 读 msg_len(4), 取 msg, 剩余是 corp_id
        msg_len = struct.unpack("!I", content[16:20])[0]
        msg = content[20:20 + msg_len].decode("utf-8")
        from_corp_id = content[20 + msg_len:].decode("utf-8")
        if from_corp_id != self._corp_id:
            raise ValueError(f"corp_id 不匹配: {from_corp_id} != {self._corp_id}")
        return msg

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """URL 验证 — 解密 echostr 返回明文."""
        sig = self._sign(timestamp, nonce, echostr)
        if sig != msg_signature:
            raise ValueError("签名验证失败")
        return self._decrypt(echostr)

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, xml_body: str) -> str:
        """解密收到的消息 XML → 明文 XML."""
        root = ET.fromstring(xml_body)
        encrypt = root.findtext("Encrypt", "")
        sig = self._sign(timestamp, nonce, encrypt)
        if sig != msg_signature:
            raise ValueError("消息签名验证失败")
        return self._decrypt(encrypt)

    def encrypt_msg(self, reply_xml: str, nonce: str = "", timestamp: str = "") -> str:
        """加密回复消息 → 密文 XML."""
        if not timestamp:
            timestamp = str(int(time.time()))
        if not nonce:
            nonce = "".join(random.choices(string.digits, k=10))
        encrypt = self._encrypt(reply_xml)
        sig = self._sign(timestamp, nonce, encrypt)
        return (
            f"<xml>"
            f"<Encrypt><![CDATA[{encrypt}]]></Encrypt>"
            f"<MsgSignature><![CDATA[{sig}]]></MsgSignature>"
            f"<TimeStamp>{timestamp}</TimeStamp>"
            f"<Nonce><![CDATA[{nonce}]]></Nonce>"
            f"</xml>"
        )


class WeChatAdapter(BasePlatformAdapter):
    """微信适配器 — 企业微信自建应用 / Wechaty / 智能机器人(WebSocket)."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.WECHAT, config)
        self._mode = config.get("mode", "work")
        self._corp_id = config.get("corp_id", "")
        self._corp_secret = config.get("corp_secret", "")
        self._agent_id = config.get("agent_id", "")
        self._callback_token = config.get("token", "")
        self._encoding_aes_key = config.get("encoding_aes_key", "")
        self._webhook_port = config.get("webhook_port", 9002)
        self._wechaty_token = config.get("wechaty_token", "")
        self._wechaty_endpoint = config.get("wechaty_endpoint", "")
        self._access_token: str = ""
        self._token_expire_time: float = 0
        self._server = None
        self._crypto: Optional[WXBizMsgCrypt] = None
        # aibot (WebSocket 长连接) 模式
        self._bot_id = config.get("bot_id", "")
        self._bot_secret = config.get("secret", "")
        self._ws_client: Any = None

    @property
    def name(self) -> str:
        if self._mode == "wechaty":
            return "微信"
        if self._mode == "aibot":
            return "企业微信智能机器人"
        return "企业微信"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": True, "voice": True, "video": False,
            "file": True, "rich_text": False,
            "interactive": self._mode == "work",
            "edit_message": False, "delete_message": False,
            "reaction": False, "thread": False, "typing_indicator": False,
        }

    # ── 企业微信 API ──

    async def _get_work_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expire_time:
            return self._access_token
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": self._corp_id, "corpsecret": self._corp_secret},
            )
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"获取企业微信 token 失败: {data}")
            self._access_token = data["access_token"]
            self._token_expire_time = now + data.get("expires_in", 7200) - 300
            return self._access_token

    async def start(self) -> None:
        if self._mode == "work":
            await self._start_work_wechat()
        elif self._mode == "wechaty":
            await self._start_wechaty()
        elif self._mode == "aibot":
            await self._start_aibot()
        else:
            raise ValueError(f"不支持的微信模式: {self._mode}")
        self._running = True

    async def _start_work_wechat(self) -> None:
        if not self._corp_id or not self._corp_secret:
            raise ValueError("企业微信 corp_id 和 corp_secret 未配置")
        self._crypto = WXBizMsgCrypt(self._callback_token, self._encoding_aes_key, self._corp_id)
        await self._get_work_token()
        self._bot_user = PlatformUser(
            user_id=self._agent_id, username="企业微信应用",
            display_name="小巨蛋智能体", is_bot=True,
        )
        from aiohttp import web
        app = web.Application()
        app.router.add_get("/wechat/callback", self._handle_verify)
        app.router.add_post("/wechat/callback", self._handle_callback)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._webhook_port)
        await site.start()
        self._server = runner
        logger.info("企业微信适配器已启动, webhook port: %d", self._webhook_port)

    async def _start_wechaty(self) -> None:
        try:
            from wechaty import Wechaty, WechatyOptions
        except ImportError:
            raise ImportError('Wechaty 依赖未安装。请运行: pip install "xjd-agent[wechat]"')
        if self._wechaty_token:
            os.environ["WECHATY_PUPPET_SERVICE_TOKEN"] = self._wechaty_token
        if self._wechaty_endpoint:
            os.environ["WECHATY_PUPPET_SERVICE_ENDPOINT"] = self._wechaty_endpoint
        bot = Wechaty(WechatyOptions(name="xjd-agent"))

        @bot.on("message")
        async def on_message(msg):
            await self._handle_wechaty_message(msg)

        @bot.on("login")
        async def on_login(contact):
            self._bot_user = PlatformUser(
                user_id=contact.contact_id, username=contact.name,
                display_name=contact.name, is_bot=False,
            )
            logger.info("微信登录成功: %s", contact.name)

        import asyncio
        asyncio.create_task(bot.start())
        logger.info("Wechaty 模式启动中...")

    async def _start_aibot(self) -> None:
        """启动企业微信智能机器人 WebSocket 长连接模式."""
        if not self._bot_id or not self._bot_secret:
            raise ValueError("企业微信智能机器人 bot_id 和 secret 未配置")
        try:
            from aibot import WSClient, WSClientOptions
        except ImportError:
            raise ImportError(
                'aibot SDK 未安装。请运行: pip install wecom-aibot-python-sdk'
            )

        options = WSClientOptions(
            bot_id=self._bot_id,
            secret=self._bot_secret,
        )
        self._ws_client = WSClient(options)
        self._bot_user = PlatformUser(
            user_id=self._bot_id,
            username="企业微信智能机器人",
            display_name="小巨蛋智能体",
            is_bot=True,
        )

        @self._ws_client.on("authenticated")
        def on_authenticated():
            logger.info("企业微信智能机器人 WebSocket 认证成功")

        @self._ws_client.on("message.text")
        def on_text_message(frame):
            import asyncio
            asyncio.ensure_future(self._handle_aibot_message(frame))

        @self._ws_client.on("message.image")
        def on_image_message(frame):
            import asyncio
            asyncio.ensure_future(self._handle_aibot_message(frame, MessageType.IMAGE))

        @self._ws_client.on("message.file")
        def on_file_message(frame):
            import asyncio
            asyncio.ensure_future(self._handle_aibot_message(frame, MessageType.FILE))

        @self._ws_client.on("message.voice")
        def on_voice_message(frame):
            import asyncio
            asyncio.ensure_future(self._handle_aibot_message(frame, MessageType.VOICE))

        @self._ws_client.on("error")
        def on_error(error):
            logger.error("企业微信智能机器人 WebSocket 错误: %s", error)

        @self._ws_client.on("disconnected")
        def on_disconnected(reason):
            logger.warning("企业微信智能机器人断开连接: %s", reason)

        @self._ws_client.on("reconnecting")
        def on_reconnecting(attempt):
            logger.info("企业微信智能机器人重连中 (第 %d 次)...", attempt)

        await self._ws_client.connect()
        logger.info("企业微信智能机器人 WebSocket 长连接已建立")

    async def stop(self) -> None:
        self._running = False
        if self._server:
            await self._server.cleanup()
            self._server = None
        if self._ws_client:
            self._ws_client.disconnect()
            self._ws_client = None
        logger.info("微信适配器已停止")

    async def send_message(self, message: OutgoingMessage) -> str:
        if self._mode == "work":
            return await self._send_work_message(message)
        if self._mode == "aibot":
            return await self._send_aibot_message(message)
        return await self._send_wechaty_message(message)

    async def _send_work_message(self, message: OutgoingMessage) -> str:
        import httpx
        token = await self._get_work_token()
        chat_id = message.chat_id
        if message.message_type == MessageType.IMAGE:
            msg_body = {"touser": chat_id, "msgtype": "image", "agentid": int(self._agent_id), "image": {"media_id": message.media_url}}
        elif message.message_type == MessageType.FILE:
            msg_body = {"touser": chat_id, "msgtype": "file", "agentid": int(self._agent_id), "file": {"media_id": message.media_url}}
        elif message.message_type == MessageType.INTERACTIVE:
            msg_body = {"touser": chat_id, "msgtype": "textcard", "agentid": int(self._agent_id), "textcard": message.interactive or {}}
        else:
            msg_body = {"touser": chat_id, "msgtype": "text", "agentid": int(self._agent_id), "text": {"content": message.content}}
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}", json=msg_body)
            data = resp.json()
        if data.get("errcode") != 0:
            logger.error("企业微信发送消息失败: %s", data)
            raise RuntimeError(f"企业微信发送消息失败: {data.get('errmsg', '')}")
        return data.get("msgid", "")

    async def _send_wechaty_message(self, message: OutgoingMessage) -> str:
        logger.warning("Wechaty send_message 需要实现")
        return ""

    # ── 企业微信智能机器人 (aibot WebSocket) ──

    async def _handle_aibot_message(
        self, frame: dict, msg_type: MessageType = MessageType.TEXT
    ) -> None:
        """处理 aibot SDK 收到的 WebSocket 消息帧."""
        try:
            body = frame.get("body", {})
            sender_info = body.get("from", {})
            sender_id = sender_info.get("userid", "")
            sender_name = sender_info.get("name", sender_id)
            chatid = body.get("chatid", "")
            chat_type_str = body.get("chattype", "single")

            if chat_type_str == "single":
                chat_type = ChatType.PRIVATE
                effective_chat_id = sender_id
            else:
                chat_type = ChatType.GROUP
                effective_chat_id = chatid

            content = ""
            if msg_type == MessageType.TEXT:
                text_info = body.get("text", {})
                content = text_info.get("content", "")
                # 去掉 @机器人 前缀
                if chat_type == ChatType.GROUP and content.startswith("@"):
                    parts = content.split(" ", 1)
                    content = parts[1] if len(parts) > 1 else ""

            sender = PlatformUser(
                user_id=sender_id,
                username=sender_name,
                display_name=sender_name,
            )
            chat = PlatformChat(
                chat_id=effective_chat_id,
                chat_type=chat_type,
                platform=PlatformType.WECHAT,
            )
            headers = frame.get("headers", {})
            platform_msg = PlatformMessage(
                message_id=headers.get("req_id", body.get("msgid", "")),
                platform=PlatformType.WECHAT,
                chat=chat,
                sender=sender,
                message_type=msg_type,
                content=content.strip(),
                timestamp=time.time(),
                raw=frame,
            )
            await self._dispatch_message(platform_msg)
        except Exception as e:
            logger.error("aibot 消息处理失败: %s", e, exc_info=True)

    async def _send_aibot_message(self, message: OutgoingMessage) -> str:
        """通过 aibot WebSocket 发送消息（主动推送）."""
        if not self._ws_client:
            logger.error("aibot WSClient 未连接")
            return ""
        try:
            body = {
                "msgtype": "markdown",
                "markdown": {"content": message.content},
            }
            result = await self._ws_client.send_message(message.chat_id, body)
            return result.get("headers", {}).get("req_id", "")
        except Exception as e:
            logger.error("aibot 发送消息失败: %s", e)
            return ""

    # ── 企业微信回调处理 (AES 加解密) ──

    async def _handle_verify(self, request: Any) -> Any:
        """企业微信 URL 验证 — 解密 echostr 返回明文."""
        from aiohttp import web
        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")
        try:
            reply = self._crypto.verify_url(msg_signature, timestamp, nonce, echostr)
            logger.info("企业微信 URL 验证成功")
            return web.Response(text=reply)
        except Exception as e:
            logger.error("企业微信 URL 验证失败: %s", e)
            return web.Response(text="验证失败", status=403)

    async def _handle_callback(self, request: Any) -> Any:
        """处理企业微信加密消息回调."""
        from aiohttp import web
        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        try:
            body = await request.text()
        except Exception:
            return web.Response(text="error", status=400)
        try:
            xml_plain = self._crypto.decrypt_msg(msg_signature, timestamp, nonce, body)
            root = ET.fromstring(xml_plain)
            msg_type = root.findtext("MsgType", "text")
            from_user = root.findtext("FromUserName", "")
            content_text = root.findtext("Content", "") or ""
            sender = PlatformUser(user_id=from_user, username=from_user)
            chat = PlatformChat(chat_id=from_user, chat_type=ChatType.PRIVATE, platform=PlatformType.WECHAT)
            platform_msg = PlatformMessage(
                message_id=root.findtext("MsgId", ""),
                platform=PlatformType.WECHAT, chat=chat, sender=sender,
                message_type=MessageType.TEXT, content=content_text.strip(),
                timestamp=time.time(), raw=xml_plain,
            )
            await self._dispatch_message(platform_msg)
        except Exception as e:
            logger.error("企业微信消息处理失败: %s", e)
        return web.Response(text="success")

    # ── Wechaty 消息处理 ──

    async def _handle_wechaty_message(self, msg: Any) -> None:
        if msg.is_self():
            return
        room = msg.room()
        talker = msg.talker()
        sender = PlatformUser(
            user_id=talker.contact_id if talker else "",
            username=talker.name if talker else "",
            display_name=talker.name if talker else "",
        )
        if room:
            chat = PlatformChat(chat_id=room.room_id, chat_type=ChatType.GROUP, title=await room.topic() if room else "", platform=PlatformType.WECHAT)
        else:
            chat = PlatformChat(chat_id=talker.contact_id if talker else "", chat_type=ChatType.PRIVATE, platform=PlatformType.WECHAT)
        msg_type_val = msg.type()
        message_type = MessageType.TEXT
        content = msg.text() or ""
        if msg_type_val == 6:
            message_type = MessageType.IMAGE
        elif msg_type_val == 1:
            message_type = MessageType.VOICE
        if room and self._bot_user:
            mention_self = await msg.mention_self()
            if not mention_self:
                return
            content = content.replace(f"@{self._bot_user.display_name}", "").strip()
        platform_msg = PlatformMessage(
            message_id=msg.message_id if hasattr(msg, "message_id") else "",
            platform=PlatformType.WECHAT, chat=chat, sender=sender,
            message_type=message_type, content=content,
            timestamp=time.time(), raw=msg,
        )
        await self._dispatch_message(platform_msg)

