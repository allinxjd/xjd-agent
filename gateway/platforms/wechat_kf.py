"""微信客服 (WeChat Customer Service) 适配器.

基于企业微信「微信客服」API，用于小程序客服场景。
用户在小程序点击"联系客服" → 消息通过回调推送到此适配器 → 智能回复或转人工。

API 文档: https://developer.work.weixin.qq.com/document/path/94739

依赖: pip install "xjd-agent[wechat]"  (cryptography, httpx)
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
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
from gateway.platforms.wechat import WXBizMsgCrypt

logger = logging.getLogger(__name__)

TRANSFER_KEYWORDS = [
    "人工", "转人工", "人工客服", "找人工", "真人",
    "投诉", "找经理", "找负责人",
]

# ── 共享 HTTP Server（多实例共用端口 9003）──

_shared_app: Optional[Any] = None
_shared_runner: Optional[Any] = None
_shared_port: int = 0
_shared_refcount: int = 0
_instance_dispatch: dict[str, "WeChatKFAdapter"] = {}


def _resolve_adapter(instance_id: str) -> "Optional[WeChatKFAdapter]":
    """Resolve adapter by instance_id, fallback to single instance if empty."""
    adapter = _instance_dispatch.get(instance_id)
    if not adapter and not instance_id and len(_instance_dispatch) == 1:
        adapter = next(iter(_instance_dispatch.values()))
    return adapter


async def _shared_handle_verify(request: Any) -> Any:
    """共享路由：URL 验证 — 根据 path 分发到对应实例."""
    from aiohttp import web
    instance_id = request.match_info.get("instance_id", "")
    adapter = _resolve_adapter(instance_id)
    if not adapter:
        return web.Response(text="unknown instance", status=404)
    return await adapter._handle_verify(request)


async def _shared_handle_callback(request: Any) -> Any:
    """共享路由：回调处理 — 根据 path 分发到对应实例."""
    from aiohttp import web
    instance_id = request.match_info.get("instance_id", "")
    adapter = _resolve_adapter(instance_id)
    if not adapter:
        return web.Response(text="unknown instance", status=404)
    return await adapter._handle_callback(request)


async def _get_shared_server(port: int) -> None:
    """获取或创建共享 HTTP server（路由在创建时一次性注册）."""
    global _shared_app, _shared_runner, _shared_port, _shared_refcount
    if _shared_app is None:
        from aiohttp import web
        _shared_app = web.Application()
        _shared_app.router.add_get("/wechat-kf/callback", _shared_handle_verify)
        _shared_app.router.add_post("/wechat-kf/callback", _shared_handle_callback)
        _shared_app.router.add_get("/wechat-kf/callback/{instance_id}", _shared_handle_verify)
        _shared_app.router.add_post("/wechat-kf/callback/{instance_id}", _shared_handle_callback)
        runner = web.AppRunner(_shared_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        _shared_runner = runner
        _shared_port = port
        logger.info("微信客服共享 HTTP server 已启动, port: %d", port)
    _shared_refcount += 1


async def _release_shared_server() -> None:
    """释放共享 server 引用，引用归零时关闭."""
    global _shared_app, _shared_runner, _shared_port, _shared_refcount
    _shared_refcount -= 1
    if _shared_refcount <= 0 and _shared_runner:
        await _shared_runner.cleanup()
        _shared_app = None
        _shared_runner = None
        _shared_port = 0
        _shared_refcount = 0
        logger.info("微信客服共享 HTTP server 已关闭")


class WeChatKFAdapter(BasePlatformAdapter):
    """微信客服适配器 — 处理小程序客服消息，支持智能回复 + 转人工通知."""

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.WECHAT_KF, config)
        self._instance_id: str = config.get("instance_id", "")
        self._instance_name: str = config.get("instance_name", "")
        self._corp_id = config.get("corp_id", "")
        self._corp_secret = config.get("corp_secret", "")
        self._kf_secret = config.get("kf_secret", "")
        self._open_kfid = config.get("open_kfid", "")
        self._callback_token = config.get("token", "")
        self._encoding_aes_key = config.get("encoding_aes_key", "")
        self._webhook_port = config.get("webhook_port", 9003)
        self._notify_contact = config.get("notify_contact", "")
        self._transfer_keywords = config.get("transfer_keywords", TRANSFER_KEYWORDS)
        self._access_token: str = ""
        self._token_expire_time: float = 0
        self._server = None
        self._crypto: Optional[WXBizMsgCrypt] = None
        self._next_cursor: str = ""
        self._sync_lock: Optional[asyncio.Lock] = None
        self._seen_msgids: dict[str, float] = {}
        self._seen_callbacks: dict[str, float] = {}
        self._rate_limited_users: dict[str, float] = {}
        self._knowledge: Optional[dict] = None
        self._knowledge_mtime: float = 0
        self._last_send_time: dict[str, float] = {}
        self._send_interval: float = 0.25

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def name(self) -> str:
        if self._instance_name:
            return f"微信客服({self._instance_name})"
        if self._instance_id:
            return f"微信客服({self._instance_id})"
        return "微信客服"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True, "image": True, "voice": False, "video": False,
            "file": False, "rich_text": False,
            "interactive": False,
            "edit_message": False, "delete_message": False,
            "reaction": False, "thread": False, "typing_indicator": False,
        }

    async def _get_token(self) -> str:
        """获取企业微信 access_token (使用客服专用 secret)."""
        now = time.time()
        if self._access_token and now < self._token_expire_time:
            return self._access_token
        import httpx
        secret = self._kf_secret or self._corp_secret
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                params={"corpid": self._corp_id, "corpsecret": secret},
            )
            data = resp.json()
            if data.get("errcode") != 0:
                raise RuntimeError(f"获取微信客服 token 失败: {data}")
            self._access_token = data["access_token"]
            self._token_expire_time = now + data.get("expires_in", 7200) - 300
            return self._access_token

    async def start(self) -> None:
        if not self._corp_id or not (self._kf_secret or self._corp_secret):
            raise ValueError("微信客服 corp_id 和 kf_secret/corp_secret 未配置")
        if not self._callback_token or not self._encoding_aes_key:
            raise ValueError("微信客服 callback token 和 encoding_aes_key 未配置")
        self._crypto = WXBizMsgCrypt(
            self._callback_token, self._encoding_aes_key, self._corp_id
        )
        await self._get_token()
        self._bot_user = PlatformUser(
            user_id=self._open_kfid or "kf",
            username="微信客服",
            display_name=self._instance_name or "智能客服",
            is_bot=True,
        )
        await _get_shared_server(self._webhook_port)
        _instance_dispatch[self._instance_id] = self
        self._running = True
        logger.info("微信客服适配器[%s]已启动, callback: %s", self._instance_id or "default", self._callback_path)

    @property
    def _callback_path(self) -> str:
        if self._instance_id:
            return f"/wechat-kf/callback/{self._instance_id}"
        return "/wechat-kf/callback"

    async def stop(self) -> None:
        self._running = False
        _instance_dispatch.pop(self._instance_id, None)
        await _release_shared_server()
        logger.info("微信客服适配器[%s]已停止", self._instance_id or "default")

    # ── 回调处理 ──

    async def _handle_verify(self, request: Any) -> Any:
        """URL 验证."""
        from aiohttp import web
        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        echostr = request.query.get("echostr", "")
        try:
            reply = self._crypto.verify_url(msg_signature, timestamp, nonce, echostr)
            logger.info("微信客服 URL 验证成功")
            return web.Response(text=reply)
        except Exception as e:
            logger.error("微信客服 URL 验证失败: %s", e)
            return web.Response(text="验证失败", status=403)

    async def _handle_callback(self, request: Any) -> Any:
        """接收微信客服事件回调，然后通过 sync_msg 拉取具体消息."""
        from aiohttp import web
        msg_signature = request.query.get("msg_signature", "")
        timestamp = request.query.get("timestamp", "")
        nonce = request.query.get("nonce", "")
        # Dedup: same msg_signature+timestamp = same event, skip
        cb_key = f"{msg_signature}:{timestamp}"
        now = time.time()
        self._seen_callbacks = {k: v for k, v in self._seen_callbacks.items() if now - v < 60}
        if cb_key in self._seen_callbacks:
            return web.Response(text="success")
        self._seen_callbacks[cb_key] = now
        try:
            body = await request.text()
        except Exception:
            return web.Response(text="error", status=400)
        try:
            xml_plain = self._crypto.decrypt_msg(msg_signature, timestamp, nonce, body)
            root = ET.fromstring(xml_plain)
            event_type = root.findtext("Event", "")
            token_val = root.findtext("Token", "")
            if event_type == "kf_msg_or_event":
                await self._sync_messages(token_val)
        except Exception as e:
            logger.error("微信客服回调处理失败: %s", e, exc_info=True)
        return web.Response(text="success")

    async def _sync_messages(self, token_val: str = "") -> None:
        """调用 sync_msg 接口拉取新消息."""
        import httpx
        if self._sync_lock is None:
            self._sync_lock = asyncio.Lock()
        async with self._sync_lock:
            access_token = await self._get_token()
            url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={access_token}"
            payload: dict[str, Any] = {"limit": 100}
            if self._next_cursor:
                payload["cursor"] = self._next_cursor
            if token_val:
                payload["token"] = token_val
            if self._open_kfid:
                payload["open_kfid"] = self._open_kfid
            async with httpx.AsyncClient(trust_env=False) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
            if data.get("errcode") != 0:
                logger.error("sync_msg 失败: %s", data)
                return
            self._next_cursor = data.get("next_cursor", "")
            msg_list = data.get("msg_list", [])
            # 清理过期的去重记录 (保留 5 分钟内的)
            now = time.time()
            self._seen_msgids = {k: v for k, v in self._seen_msgids.items() if now - v < 300}
            for msg in msg_list:
                msgid = msg.get("msgid", "")
                if msgid and msgid in self._seen_msgids:
                    continue
                if msgid:
                    self._seen_msgids[msgid] = now
                await self._process_kf_message(msg)

    # ── 知识库 + 欢迎语 ──

    def _get_knowledge_path(self) -> Path:
        """知识库文件路径 — 多实例时按 instance_id 隔离."""
        if self._instance_id:
            from agent.core.config import get_home
            kb_path = get_home() / "skills" / "wechat-kf" / f"kb_{self._instance_id}.json"
            if kb_path.exists():
                return kb_path
        return Path(__file__).parent.parent.parent / "agent" / "builtin_skills" / "wechat-kf" / "cs_knowledge.json"

    def _load_knowledge(self) -> dict:
        """加载知识库（带文件修改时间缓存）."""
        kb_path = self._get_knowledge_path()
        if not kb_path.exists():
            return {}
        try:
            mtime = kb_path.stat().st_mtime
            if self._knowledge and mtime == self._knowledge_mtime:
                return self._knowledge
            self._knowledge = json.loads(kb_path.read_text(encoding="utf-8"))
            self._knowledge_mtime = mtime
            return self._knowledge
        except Exception as e:
            logger.warning("加载知识库失败: %s", e)
            return self._knowledge or {}

    def _is_business_hours(self) -> bool:
        """判断当前是否在工作时间内."""
        kb = self._load_knowledge()
        hours = kb.get("business_hours", {})
        start = hours.get("start", 9)
        end = hours.get("end", 22)
        now = datetime.datetime.now()
        return start <= now.hour < end

    def _get_greeting(self) -> str:
        """获取欢迎语（区分工作时间/非工作时间）."""
        kb = self._load_knowledge()
        templates = kb.get("templates", {})
        if self._is_business_hours():
            return templates.get("greeting", "您好！请问有什么可以帮您？")
        return templates.get("outside_hours", "当前非工作时间，您的消息我们已收到，工作时间会尽快回复。")

    async def _process_kf_message(self, msg: dict) -> None:
        """处理单条客服消息."""
        origin = msg.get("origin", 0)
        # origin: 3=微信客户发送, 4=系统, 5=客服发送
        if origin != 3:
            return
        msgtype = msg.get("msgtype", "")
        external_userid = msg.get("external_userid", "")
        open_kfid = msg.get("open_kfid", "")
        content = ""
        msg_type = MessageType.TEXT
        if msgtype == "text":
            content = msg.get("text", {}).get("content", "")
        elif msgtype == "image":
            content = msg.get("image", {}).get("media_id", "")
            msg_type = MessageType.IMAGE
        elif msgtype == "event":
            event_type = msg.get("event", {}).get("event_type", "")
            if event_type == "enter_session":
                greeting = self._get_greeting()
                await self._send_kf_text(external_userid, open_kfid, greeting)
                return
            else:
                return
        else:
            content = f"[{msgtype}消息]"
        if not content:
            return
        # 检查是否需要转人工
        if self._should_transfer(content):
            await self._handle_transfer(external_userid, open_kfid, content)
            return
        sender = PlatformUser(
            user_id=external_userid,
            username=external_userid,
            display_name=external_userid,
        )
        chat = PlatformChat(
            chat_id=f"{open_kfid}:{external_userid}",
            chat_type=ChatType.PRIVATE,
            platform=PlatformType.WECHAT_KF,
        )
        platform_msg = PlatformMessage(
            message_id=msg.get("msgid", ""),
            platform=PlatformType.WECHAT_KF,
            chat=chat,
            sender=sender,
            message_type=msg_type,
            content=content.strip(),
            timestamp=msg.get("send_time", time.time()),
            raw=msg,
            metadata={"instance_id": self._instance_id} if self._instance_id else {},
        )
        await self._dispatch_message(platform_msg)

    def _should_transfer(self, content: str) -> bool:
        """检测是否包含转人工关键词."""
        kb = self._load_knowledge()
        keywords = kb.get("transfer_keywords", self._transfer_keywords)
        content_lower = content.lower()
        return any(kw in content_lower for kw in keywords)

    async def _handle_transfer(
        self, external_userid: str, open_kfid: str, content: str
    ) -> None:
        """转人工：回复用户 + 通知人工客服."""
        kb = self._load_knowledge()
        transfer_msg = kb.get("templates", {}).get(
            "transfer", "好的，正在为您转接人工客服，请稍候。工作时间内会尽快回复您。"
        )
        await self._send_kf_text(external_userid, open_kfid, transfer_msg)
        # 通知人工（通过 gateway 事件，由 wechat_clawbot 发送到个人微信）
        event = PlatformEvent(
            event_type=EventType.CUSTOM,
            platform=PlatformType.WECHAT_KF,
            data={
                "type": "transfer_to_human",
                "external_userid": external_userid,
                "open_kfid": open_kfid,
                "content": content,
                "notify_contact": self._notify_contact,
            },
            timestamp=time.time(),
        )
        await self._dispatch_event(event)
        logger.info("转人工: user=%s, content=%s", external_userid, content)

    # ── 发送消息 ──

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送客服消息给用户."""
        # chat_id 格式: "open_kfid:external_userid"
        parts = message.chat_id.split(":", 1)
        if len(parts) == 2:
            open_kfid, external_userid = parts
        else:
            external_userid = message.chat_id
            open_kfid = self._open_kfid
        if message.message_type == MessageType.IMAGE:
            return await self._send_kf_image(external_userid, open_kfid, message.media_url or "")
        return await self._send_kf_text(external_userid, open_kfid, message.content)

    async def _send_kf_text(
        self, external_userid: str, open_kfid: str, content: str
    ) -> str:
        """通过微信客服 API 发送文本消息."""
        import httpx
        # Skip if user is in rate-limit cooldown (30s)
        now = time.time()
        cooldown_until = self._rate_limited_users.get(external_userid, 0)
        if now < cooldown_until:
            logger.debug("Skipping send to rate-limited user %s", external_userid)
            return ""
        # Throttle: min 250ms between sends to same user
        key = f"{open_kfid}:{external_userid}"
        last = self._last_send_time.get(key, 0)
        wait = self._send_interval - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_send_time[key] = time.time()
        token = await self._get_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={token}"
        payload = {
            "touser": external_userid,
            "open_kfid": open_kfid,
            "msgtype": "text",
            "text": {"content": content},
        }
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
        if data.get("errcode") != 0:
            errcode = data.get("errcode")
            if errcode == 95001:
                self._rate_limited_users[external_userid] = time.time() + 30
                logger.warning("微信客服发送限流(95001), user=%s, cooldown 30s", external_userid)
            else:
                logger.error("微信客服发送消息失败: %s", data)
            return ""
        return data.get("msgid", "")

    async def _send_kf_image(
        self, external_userid: str, open_kfid: str, media_id: str
    ) -> str:
        """通过微信客服 API 发送图片消息."""
        import httpx
        token = await self._get_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={token}"
        payload = {
            "touser": external_userid,
            "open_kfid": open_kfid,
            "msgtype": "image",
            "image": {"media_id": media_id},
        }
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
        if data.get("errcode") != 0:
            logger.error("微信客服发送图片失败: %s", data)
            return ""
        return data.get("msgid", "")
