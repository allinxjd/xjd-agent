"""拼多多客服 WebSocket 实时客户端.

参考 JC0v0/Customer-Agent 的 WebSocket 方案，
通过 mms.pinduoduo.com 的 WebSocket 接口实现实时客服消息收发。

认证流程: BrowserSessionManager cookies → getToken → WebSocket connect
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from enum import IntEnum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

WS_URL = "wss://m-ws.pinduoduo.com/"
TOKEN_URL = "https://mms.pinduoduo.com/chats/getToken"
SEND_MSG_URL = "https://mms.pinduoduo.com/plateau/chat/send_message"
SEND_CARD_URL = "https://mms.pinduoduo.com/plateau/message/send/mallGoodsCard"
CS_LIST_URL = "https://mms.pinduoduo.com/plateau/chat/getAssignCsList"


class PddMsgType(IntEnum):
    TEXT = 0
    IMAGE = 1
    VIDEO = 14
    EMOTION = 5
    GOODS_SPEC = 64
    TRANSFER = 24
    WITHDRAW = 1002


_IGNORE_MSG_TYPES = {
    20, 30, 31, 53, 67,
}


class PddCSMessage:
    """解析后的 PDD 客服消息."""

    __slots__ = ("msg_id", "msg_type", "from_uid", "from_role", "to_uid",
                 "content", "timestamp", "goods_info", "order_info", "raw")

    def __init__(self, data: dict[str, Any]) -> None:
        msg = data.get("message", {})
        self.msg_id = msg.get("msg_id", "")
        self.msg_type = msg.get("type", 0)
        self.from_uid = msg.get("from", {}).get("uid", "")
        self.from_role = msg.get("from", {}).get("role", "")
        self.to_uid = msg.get("to", {}).get("uid", "")
        self.content = msg.get("content", "")
        self.timestamp = msg.get("time", time.time())
        self.goods_info = self._extract_goods(msg)
        self.order_info = self._extract_order(msg)
        self.raw = data

    @property
    def is_customer(self) -> bool:
        return self.from_role == "user"

    @staticmethod
    def _extract_goods(msg: dict) -> Optional[dict]:
        info = msg.get("info", {})
        gid = info.get("goodsID") or info.get("data", {}).get("goodsID")
        if not gid:
            return None
        return {
            "goods_id": gid,
            "goods_name": info.get("goodsName") or info.get("data", {}).get("goodsName", ""),
            "goods_price": info.get("goodsPrice") or info.get("data", {}).get("goodsPrice", ""),
        }

    @staticmethod
    def _extract_order(msg: dict) -> Optional[dict]:
        info = msg.get("info", {})
        oid = info.get("orderSequenceNo")
        if not oid:
            return None
        return {
            "order_id": oid,
            "goods_name": info.get("goodsName", ""),
            "after_sales_status": info.get("afterSalesStatus"),
        }


class PddCSClient:
    """PDD 客服 WebSocket 客户端."""

    def __init__(self, cookies: dict[str, str], shop_id: str = "") -> None:
        self._cookies = cookies
        self._shop_id = shop_id
        self._ws: Any = None
        self._ws_url: Optional[str] = None
        self._running = False
        self._queue: asyncio.Queue[PddCSMessage] = asyncio.Queue(maxsize=1000)
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._reply_engine: Any = None
        self._reconnect_attempts = 0
        self._max_reconnect = 5
        self._reconnect_lock = asyncio.Lock()
        self._standby = False
        self._standby_task: Optional[asyncio.Task] = None
        self._standby_interval = 120
        self._stats = {"received": 0, "replied": 0, "transferred": 0, "errors": 0}
        self.on_offline: Optional[Callable[[str, str, str], Any]] = None
        self._graceful_disconnect = False

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._running

    @property
    def standby(self) -> bool:
        return self._standby

    @property
    def mall_id(self) -> str:
        return self._shop_id

    @property
    def queue(self) -> asyncio.Queue[PddCSMessage]:
        return self._queue

    def set_reply_engine(self, engine: Any) -> None:
        self._reply_engine = engine

    async def get_token(self) -> tuple[Optional[str], Optional[str]]:
        """获取 WebSocket token 和 mall_id. Returns (token, mall_id)."""
        try:
            import aiohttp
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            logger.info("PDD getToken: cookies count=%d", len(self._cookies))
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    TOKEN_URL,
                    headers={"Cookie": cookie_str},
                    json={"version": "3"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    logger.info("PDD getToken response: status=%d, keys=%s", resp.status, list(data.keys()))
                    token = (
                        data.get("result", {}).get("access_token")
                        or data.get("access_token")
                        or data.get("token")
                    )
                    mall_id = (
                        data.get("mall_id")
                        or data.get("result", {}).get("mall_id")
                    )
                    ws_url = data.get("use_ip") or data.get("result", {}).get("use_ip")
                    if ws_url:
                        self._ws_url = ws_url
                        logger.info("PDD getToken: ws_url=%s", ws_url)
                    if not token:
                        logger.warning("PDD getToken: no token in response: %s", str(data)[:200])
                    else:
                        logger.info("PDD getToken: got token, mall_id=%s", mall_id or "?")
                    return token, str(mall_id) if mall_id else None
        except Exception as e:
            logger.error("PDD getToken failed: %s", e)
            return None, None

    async def connect(self) -> bool:
        token, mall_id = await self.get_token()
        if not token:
            logger.error("PDD CS: 无法获取 token")
            return False
        if mall_id and not self._shop_id:
            self._shop_id = mall_id
        try:
            import websockets
            # API 返回的 use_ip (ws://...:88) 不可用，始终用 wss
            url = f"{WS_URL}?access_token={token}&role=mall_cs&client=web&version=3"
            self._ws = await websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=20,
                max_size=10 * 1024 * 1024,
                close_timeout=10,
            )
            self._running = True
            self._reconnect_attempts = 0
            self._recv_task = asyncio.create_task(self._recv_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            if self._reply_engine:
                self._consumer_task = asyncio.create_task(self._consumer_loop())
            logger.info("PDD CS WebSocket connected (shop=%s)", self._shop_id)
            return True
        except Exception as e:
            logger.error("PDD CS WebSocket connect failed: %s", e)
            return False

    async def disconnect(self) -> None:
        self._graceful_disconnect = True
        self._running = False
        self._standby = False
        for task in (self._recv_task, self._heartbeat_task, self._consumer_task, self._standby_task):
            if task and not task.done():
                task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("PDD CS WebSocket disconnected")

    async def _recv_loop(self) -> None:
        logger.info("PDD CS _recv_loop started")
        while self._running and self._ws:
            try:
                raw = await self._ws.recv()
                logger.info("PDD CS raw recv: %s", str(raw)[:200])
                data = json.loads(raw)
                if data.get("response") == "system_push" and data.get("message", {}).get("type") == 30:
                    kick_msg = data.get("message", {}).get("content", "")
                    logger.warning("PDD CS: 被踢下线 — %s (进入待命模式，%ds 后尝试重连)", kick_msg, self._standby_interval)
                    await self._enter_standby()
                    break
                msg = PddCSMessage(data)
                logger.info("PDD CS raw msg: role=%s, uid=%s, type=%s, content=%s",
                            msg.from_role, msg.from_uid, msg.msg_type, (msg.content or "")[:30])
                if msg.is_customer and msg.content and msg.msg_type not in _IGNORE_MSG_TYPES:
                    self._stats["received"] += 1
                    logger.info("PDD CS 收到买家消息: uid=%s, content=%s", msg.from_uid, msg.content[:50])
                    try:
                        self._queue.put_nowait(msg)
                    except asyncio.QueueFull:
                        try:
                            self._queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        self._queue.put_nowait(msg)
                        logger.warning("PDD CS 消息队列已满，丢弃最旧消息")
            except json.JSONDecodeError as e:
                logger.warning("PDD CS recv: malformed JSON: %s", e)
                continue
            except Exception as e:
                if self._running:
                    logger.warning("PDD CS recv error: %s", e)
                    await self._try_reconnect()
                break
        logger.info("PDD CS _recv_loop exited (running=%s, ws=%s)", self._running, self._ws is not None)

    async def _consumer_loop(self) -> None:
        """从队列取消息 → AutoReplyEngine 生成回复 → send_text 发回."""
        logger.info("PDD CS _consumer_loop started")
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                result = await self._reply_engine.handle_message(
                    uid=msg.from_uid,
                    content=msg.content,
                    goods_info=msg.goods_info,
                    order_info=msg.order_info,
                )
                action = result.get("action", "")
                reply = result.get("reply", "")
                logger.info("PDD CS 自动回复: uid=%s, action=%s, category=%s, reply=%s",
                            msg.from_uid, action, result.get("category"), reply[:50] if reply else "")

                if result.get("transfer"):
                    self._stats["transferred"] += 1
                    ok = await self.transfer_to_human(msg.from_uid)
                    if not ok:
                        await self.send_text(msg.from_uid, reply)
                elif reply:
                    ok = await self.send_text(msg.from_uid, reply)
                    if ok:
                        self._stats["replied"] += 1
                    else:
                        self._stats["errors"] += 1
                        logger.warning("PDD CS 发送回复失败: uid=%s", msg.from_uid)
            except Exception as e:
                self._stats["errors"] += 1
                logger.error("PDD CS consumer error: %s", e)

    async def _heartbeat_loop(self) -> None:
        while self._running and self._ws:
            try:
                await asyncio.sleep(60)
                if self._ws:
                    await self._ws.ping()
            except Exception as e:
                if self._running:
                    logger.warning("PDD CS heartbeat failed: %s — triggering reconnect", e)
                    asyncio.create_task(self._try_reconnect())
                break

    async def _enter_standby(self) -> None:
        """被踢下线后进入待命模式，定期尝试重连."""
        for task in (self._recv_task, self._heartbeat_task, self._consumer_task):
            if task and not task.done():
                task.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._standby = True
        if self._standby_task and not self._standby_task.done():
            return
        self._standby_task = asyncio.create_task(self._standby_loop())

    async def _standby_loop(self) -> None:
        logger.info("PDD CS: 进入待命模式，每 %ds 尝试重连", self._standby_interval)
        while self._running and self._standby:
            try:
                await asyncio.sleep(self._standby_interval)
            except asyncio.CancelledError:
                break
            if not self._running or not self._standby:
                break
            async with self._reconnect_lock:
                if not self._running or not self._standby:
                    break
                logger.info("PDD CS: 待命重连尝试...")
                token, mall_id = await self.get_token()
                if mall_id and mall_id != self._shop_id:
                    logger.info("PDD CS: mall_id 变更 %s → %s", self._shop_id, mall_id)
                    self._shop_id = mall_id
                if not token:
                    logger.info("PDD CS: 待命重连 — token 获取失败，继续等待")
                    continue
                try:
                    import websockets
                    url = f"{WS_URL}?access_token={token}&role=mall_cs&client=web&version=3"
                    ws = await asyncio.wait_for(
                        websockets.connect(
                            url, ping_interval=20, ping_timeout=20,
                            max_size=10 * 1024 * 1024, close_timeout=10,
                        ),
                        timeout=15,
                    )
                    if not self._running or not self._standby:
                        await ws.close()
                        break
                    for task in (self._recv_task, self._heartbeat_task, self._consumer_task):
                        if task and not task.done():
                            task.cancel()
                    self._ws = ws
                    self._standby = False
                    self._reconnect_attempts = 0
                    self._recv_task = asyncio.create_task(self._recv_loop())
                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    if self._reply_engine:
                        self._consumer_task = asyncio.create_task(self._consumer_loop())
                    logger.info("PDD CS: 待命重连成功，自动接手客服")
                    return
                except Exception as e:
                    logger.info("PDD CS: 待命重连失败 — %s，继续等待", e)
        self._standby = False
        if not self._running and not self._graceful_disconnect and self.on_offline:
            asyncio.create_task(self._fire_offline("standby_exit", "待命模式退出，客服已离线"))
        logger.info("PDD CS: 退出待命模式")

    async def _try_reconnect(self) -> None:
        if self._reconnect_lock.locked():
            return
        async with self._reconnect_lock:
            if not self._running:
                return
            if self._reconnect_attempts >= self._max_reconnect:
                logger.error("PDD CS: 达到最大重连次数 (%d)", self._max_reconnect)
                self._running = False
                if self.on_offline:
                    asyncio.create_task(self._fire_offline("reconnect_exhausted", f"重连 {self._max_reconnect} 次均失败"))
                return
            self._reconnect_attempts += 1
            delay = min(2 ** self._reconnect_attempts, 60)
            logger.info("PDD CS: %ds 后重连 (第 %d 次)", delay, self._reconnect_attempts)
            await asyncio.sleep(delay)
            if not self._running:
                return
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
                self._ws = None
            await self.connect()

    async def _fire_offline(self, reason: str, detail: str) -> None:
        try:
            if self.on_offline:
                result = self.on_offline(self._shop_id, reason, detail)
                if asyncio.iscoroutine(result):
                    await result
        except Exception as e:
            logger.warning("on_offline callback failed: %s", e)

    def _build_send_payload(self, uid: str, content: str, msg_type: int = 0) -> dict:
        return {
            "data": {
                "cmd": "send_message",
                "request_id": str(uuid.uuid4()),
                "message": {
                    "to": {"role": "user", "uid": uid},
                    "from": {"role": "mall_cs"},
                    "content": content,
                    "msg_id": None,
                    "type": msg_type,
                    "is_aut": 0,
                    "manual_reply": 1,
                },
            },
            "client": "WEB",
        }

    async def send_text(self, uid: str, content: str) -> bool:
        return await self._send_message(uid, content, msg_type=0)

    async def send_image(self, uid: str, image_url: str) -> bool:
        return await self._send_message(uid, image_url, msg_type=1)

    async def _send_message(self, uid: str, content: str, msg_type: int = 0) -> bool:
        for attempt in range(2):
            try:
                import aiohttp
                cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
                payload = self._build_send_payload(uid, content, msg_type)
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        SEND_MSG_URL,
                        headers={"Cookie": cookie_str, "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        data = await resp.json()
                        if data.get("success"):
                            return True
                        logger.warning("PDD send_message failed (attempt %d): %s", attempt + 1, data)
            except Exception as e:
                logger.error("PDD send_message error (attempt %d): %s", attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(1)
        return False

    async def send_product_card(self, uid: str, goods_id: str) -> bool:
        try:
            import aiohttp
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    SEND_CARD_URL,
                    headers={"Cookie": cookie_str, "Content-Type": "application/json"},
                    json={"uid": uid, "goods_id": goods_id, "biz_type": 2},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    return bool(data.get("success"))
        except Exception as e:
            logger.error("PDD send_product_card error: %s", e)
            return False

    async def transfer_to_human(self, uid: str) -> bool:
        try:
            import aiohttp
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    CS_LIST_URL,
                    headers={"Cookie": cookie_str, "Content-Type": "application/json"},
                    json={},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    cs_list = data.get("result", {}).get("cs_list", [])
                    available = [cs for cs in cs_list if cs.get("online")]
                    if not available:
                        logger.warning("PDD: 无在线人工客服")
                        return False
                    target_cs = available[0].get("uid", "")
                    payload = self._build_send_payload(uid, "", msg_type=24)
                    payload["data"]["message"]["transfer_to"] = target_cs
                    async with session.post(
                        SEND_MSG_URL,
                        headers={"Cookie": cookie_str, "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp2:
                        result = await resp2.json()
                        return bool(result.get("success"))
        except Exception as e:
            logger.error("PDD transfer_to_human error: %s", e)
            return False
