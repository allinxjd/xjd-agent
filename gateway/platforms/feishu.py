"""飞书 (Feishu/Lark) 适配器 — 支持 Webhook 和长连接两种模式.

功能:
- 接收文本/图片/文件/富文本消息
- 发送文本/图片/交互式卡片消息
- 群聊 @机器人 触发
- 事件订阅 (消息事件、成员变更等)
- Webhook 验证
- 长连接模式 (无需公网 IP)

依赖: pip install "xjd-agent[feishu]"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

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

class FeishuAdapter(BasePlatformAdapter):
    """飞书机器人适配器.

    配置 (config dict):
        app_id: str              # 飞书应用 App ID
        app_secret: str          # 飞书应用 App Secret
        verification_token: str  # 事件订阅验证 Token (webhook 模式必填)
        encrypt_key: str         # (可选) 加密密钥
        mode: str                # "webhook" (默认) 或 "long_poll" (长连接, 无需公网IP)
        webhook_port: int        # (可选) Webhook 服务端口, 默认 9001

    飞书开放平台配置:
        1. 创建企业自建应用
        2. 开启机器人能力
        3. Webhook 模式: 配置事件订阅 URL: http://your-domain:9001/feishu/webhook
           长连接模式: 无需配置 URL, 适配器主动连接飞书服务器
        4. 订阅 im.message.receive_v1 事件
        5. 添加权限: im:message, im:message:send_as_bot
    """

    def __init__(self, config: dict[str, Any]) -> None:
        super().__init__(PlatformType.FEISHU, config)
        self._app_id = config.get("app_id", "")
        self._app_secret = config.get("app_secret", "")
        self._verification_token = config.get("verification_token", "")
        self._encrypt_key = config.get("encrypt_key", "")
        self._mode = config.get("mode", "webhook")  # "webhook" or "long_poll"
        self._webhook_port = config.get("webhook_port", 9001)
        self._tenant_access_token: str = ""
        self._token_expire_time: float = 0
        self._server = None  # aiohttp web server
        self._http_client = None  # 共享 httpx.AsyncClient
        self._ws_task = None  # 长连接任务
        self._ws_watchdog_task = None  # 长连接 watchdog
        self._last_sdk_activity: float = 0  # SDK 最后活动时间
        # 事件去重缓存: event_id → timestamp
        self._processed_events: dict[str, float] = {}
        self._dedup_ttl: float = 300.0  # 5 分钟

    @property
    def name(self) -> str:
        return "飞书"

    @property
    def capabilities(self) -> dict[str, bool]:
        return {
            "text": True,
            "image": True,
            "voice": True,
            "video": False,
            "file": True,
            "rich_text": True,      # 飞书富文本
            "interactive": True,     # 飞书消息卡片
            "edit_message": True,
            "delete_message": True,
            "reaction": True,
            "thread": True,          # 话题回复
            "typing_indicator": False,
        }

    async def _ensure_http_client(self):
        """确保共享 HTTP 客户端已创建."""
        if self._http_client is None:
            import httpx
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def _get_tenant_token(self) -> str:
        """获取 tenant_access_token (自动缓存和刷新, 带重试)."""
        now = time.time()
        if self._tenant_access_token and now < self._token_expire_time:
            return self._tenant_access_token

        client = await self._ensure_http_client()

        last_error = None
        for attempt in range(3):
            try:
                resp = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={"app_id": self._app_id, "app_secret": self._app_secret},
                )
                data = resp.json()
                if data.get("code") != 0:
                    raise RuntimeError(f"获取飞书 token 失败: {data}")

                self._tenant_access_token = data["tenant_access_token"]
                self._token_expire_time = now + data.get("expire", 7200) - 300
                return self._tenant_access_token
            except Exception as e:
                last_error = e
                if attempt < 2:
                    import asyncio
                    await asyncio.sleep(1.0 * (attempt + 1))
                    logger.warning("飞书 token 获取重试 %d/3: %s", attempt + 1, e)

        raise last_error  # type: ignore

    async def _api_request(
        self,
        method: str,
        path: str,
        json_data: Optional[dict] = None,
        raw_response: bool = False,
    ) -> Any:
        """发送飞书 API 请求 (使用共享 HTTP 客户端)."""
        client = await self._ensure_http_client()
        token = await self._get_tenant_token()
        url = f"https://open.feishu.cn/open-apis{path}"

        resp = await client.request(
            method,
            url,
            json=json_data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )

        if raw_response:
            return resp

        return resp.json()

    async def _download_resource(
        self, message_id: str, file_key: str, resource_type: str = "file",
    ) -> bytes:
        """下载飞书消息中的资源文件 (图片/音频/文件).

        Args:
            message_id: 消息 ID
            file_key: 资源 key (image_key / file_key)
            resource_type: "image" | "file"
        """
        client = await self._ensure_http_client()
        token = await self._get_tenant_token()
        url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}"

        resp = await client.get(
            url,
            params={"type": resource_type},
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        return resp.content

    async def start(self) -> None:
        """启动飞书适配器 (根据 mode 选择 webhook 或长连接)."""
        if not self._app_id or not self._app_secret:
            raise ValueError("飞书 app_id 和 app_secret 未配置")

        # 初始化共享 HTTP 客户端
        await self._ensure_http_client()

        # 验证 token
        await self._get_tenant_token()

        # 获取 Bot 信息
        bot_info = await self._api_request("GET", "/bot/v3/info")
        if bot_info.get("code") == 0:
            bot = bot_info.get("bot", {})
            self._bot_user = PlatformUser(
                user_id=bot.get("open_id", ""),
                username=bot.get("app_name", ""),
                display_name=bot.get("app_name", ""),
                is_bot=True,
            )

        if self._mode == "long_poll":
            await self._start_long_poll()
        else:
            await self._start_webhook()

    async def _start_webhook(self) -> None:
        """启动 Webhook HTTP 服务."""
        try:
            from aiohttp import web
        except ImportError:
            raise ImportError("aiohttp 未安装。请运行: pip install aiohttp")

        app = web.Application()
        app.router.add_post("/feishu/webhook", self._handle_webhook)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self._webhook_port)
        await site.start()

        self._server = runner
        self._running = True
        logger.info(
            "飞书适配器已启动 [webhook]: %s, port: %d",
            self._bot_user.display_name if self._bot_user else "unknown",
            self._webhook_port,
        )

    async def _start_long_poll(self) -> None:
        """启动长连接模式 — 使用飞书官方 SDK lark_oapi.ws.Client."""
        import asyncio
        import threading
        import time as _time

        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as ws_mod
        except ImportError:
            raise ImportError("长连接模式需要 lark-oapi。请运行: pip install lark-oapi")

        self._running = True
        self._event_loop = asyncio.get_event_loop()
        self._last_sdk_activity = _time.time()

        app_id = self._app_id
        app_secret = self._app_secret
        encrypt_key = self._encrypt_key or ""
        verification_token = self._verification_token or ""

        def _run_ws():
            """带自动重连的 SDK 长连接线程.

            不依赖 SDK 内置重连 (重连后事件回调可能失效),
            而是每次 start() 退出后创建全新 Client 重试。
            """
            import os
            for k in ("ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy",
                       "HTTP_PROXY", "http_proxy"):
                os.environ.pop(k, None)

            try:
                import websockets
                _OrigConnect = websockets.connect

                class _DirectConnect(_OrigConnect):
                    def __init__(self, *args, **kwargs):
                        kwargs.setdefault("proxy", None)
                        super().__init__(*args, **kwargs)

                websockets.connect = _DirectConnect
            except Exception:
                pass

            while self._running:
                try:
                    new_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(new_loop)
                    ws_mod.loop = new_loop

                    # 每次重连都重新构建 event_handler + Client (全新状态)
                    handler = lark.EventDispatcherHandler.builder(
                        encrypt_key, verification_token,
                    ).register_p2_im_message_receive_v1(
                        self._on_sdk_message
                    ).build()

                    ws_client = lark.ws.Client(
                        app_id, app_secret,
                        event_handler=handler,
                        log_level=lark.LogLevel.INFO,
                    )
                    self._ws_client = ws_client
                    self._last_sdk_activity = _time.time()
                    logger.info("飞书长连接启动 (新 Client)")
                    ws_client.start()  # 阻塞直到连接彻底断开
                except Exception as e:
                    logger.error("飞书长连接异常退出: %s", e)

                self._ws_client = None
                if self._running:
                    logger.info("飞书长连接断开，5秒后重连...")
                    _time.sleep(5)

            logger.info("飞书长连接线程退出")

        self._ws_thread = threading.Thread(target=_run_ws, daemon=True)
        self._ws_thread.start()

        # Watchdog: 检测 SDK 重连后事件失效的情况
        async def _watchdog():
            """每 60 秒检查一次, 如果 10 分钟没有 SDK 活动且线程还活着, 强制重启."""
            while self._running:
                await asyncio.sleep(60)
                if not self._running:
                    break
                elapsed = _time.time() - self._last_sdk_activity
                if elapsed > 600 and self._ws_thread and self._ws_thread.is_alive():
                    logger.warning("飞书 SDK 超过 %.0f 秒无活动, 强制重启连接", elapsed)
                    # 杀掉当前连接, 让 _run_ws 的 while 循环创建新 Client
                    if self._ws_client:
                        try:
                            self._ws_client._conn = None
                        except Exception:
                            pass
                    self._last_sdk_activity = _time.time()

        self._ws_watchdog_task = asyncio.create_task(_watchdog())

        await asyncio.sleep(2)
        logger.info(
            "飞书适配器已启动 [长连接/官方SDK]: %s",
            self._bot_user.display_name if self._bot_user else "unknown",
        )

    def _on_sdk_message(self, data) -> None:
        """官方 SDK 长连接收到消息的回调 (同步, 在 SDK 线程中执行)."""
        import time as _time
        self._last_sdk_activity = _time.time()
        logger.debug("飞书 SDK 回调触发: %s", type(data).__name__)
        try:
            event = data.event
            if not event or not event.message:
                return

            msg = event.message
            sender_info = event.sender

            # 构造与 webhook 模式相同的 event dict, 复用 _handle_message_event
            event_dict = {
                "message": {
                    "message_id": msg.message_id or "",
                    "chat_id": msg.chat_id or "",
                    "chat_type": msg.chat_type or "p2p",
                    "message_type": msg.message_type or "text",
                    "content": msg.content or "{}",
                    "parent_id": msg.parent_id,
                    "mentions": [],
                },
                "sender": {
                    "sender_id": {
                        "open_id": sender_info.sender_id.open_id if sender_info and sender_info.sender_id else "",
                        "user_id": sender_info.sender_id.user_id if sender_info and sender_info.sender_id else "",
                    }
                },
            }

            # SDK mentions 转换
            if msg.mentions:
                for m in msg.mentions:
                    event_dict["message"]["mentions"].append({
                        "id": {"open_id": m.id.open_id if m.id else ""},
                        "name": m.name or "",
                    })

            # 跨线程调度到主事件循环 (fire-and-forget, 不阻塞 SDK 回调线程)
            import asyncio

            def _on_done(fut):
                try:
                    fut.result()
                except Exception as err:
                    logger.error("飞书消息处理失败: %s", err, exc_info=True)

            future = asyncio.run_coroutine_threadsafe(
                self._handle_message_event(event_dict),
                self._event_loop,
            )
            future.add_done_callback(_on_done)

        except Exception as e:
            logger.error("飞书长连接消息处理异常: %s (type=%s)", e, type(e).__name__, exc_info=True)

    async def stop(self) -> None:
        """停止飞书适配器."""
        self._running = False
        # 停止 watchdog
        if hasattr(self, '_ws_watchdog_task') and self._ws_watchdog_task:
            self._ws_watchdog_task.cancel()
            self._ws_watchdog_task = None
        # 停止官方 SDK 长连接
        if hasattr(self, '_ws_client') and self._ws_client:
            try:
                self._ws_client._conn = None  # 触发断开
            except Exception:
                pass
            self._ws_client = None
        # 等待 WS 线程退出 (最多 2 秒)
        if hasattr(self, '_ws_thread') and self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=2)
            if self._ws_thread.is_alive():
                logger.debug("飞书 WS 线程未在 2 秒内退出, 跳过 (daemon 线程会随进程退出)")
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        if self._server:
            await self._server.cleanup()
            self._server = None
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        logger.info("飞书适配器已停止")

    async def send_message(self, message: OutgoingMessage) -> str:
        """发送消息到飞书."""
        chat_id = message.chat_id

        if message.message_type == MessageType.INTERACTIVE:
            # 交互式卡片
            msg_type = "interactive"
            content = json.dumps(message.interactive or {}, ensure_ascii=False)
        elif message.message_type == MessageType.IMAGE:
            msg_type = "image"
            # 需要先上传图片获取 image_key
            content = json.dumps({"image_key": message.media_url})
        elif message.message_type == MessageType.VOICE:
            # 语音消息: 上传音频 → 发送 audio 消息
            try:
                file_key = await self._upload_audio(message.media_data)
                msg_type = "audio"
                content = json.dumps({"file_key": file_key})
            except Exception as e:
                logger.warning("飞书语音上传失败，降级为文本: %s", e)
                msg_type = "text"
                content = json.dumps({"text": message.content or "[语音消息]"}, ensure_ascii=False)
        elif message.message_type == MessageType.RICH_TEXT:
            msg_type = "post"
            content = json.dumps({
                "zh_cn": {
                    "title": "",
                    "content": [[{"tag": "text", "text": message.content}]],
                }
            }, ensure_ascii=False)
        else:
            # 纯文本
            msg_type = "text"
            content = json.dumps({"text": message.content}, ensure_ascii=False)

        body = {
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": content,
        }

        if message.reply_to_id:
            body["reply_in_thread"] = True

        # 根据 chat_id 格式选择 receive_id_type
        # oc_ 开头是 chat_id, ou_ 开头是 open_id
        if chat_id.startswith("oc_"):
            receive_id_type = "chat_id"
        else:
            receive_id_type = "open_id"

        result = await self._api_request(
            "POST",
            f"/im/v1/messages?receive_id_type={receive_id_type}",
            json_data=body,
        )

        if result.get("code") != 0:
            logger.error("飞书发送消息失败: %s", result)
            raise RuntimeError(f"飞书发送消息失败: {result.get('msg', '')}")

        msg_id = result.get("data", {}).get("message_id", "")
        return msg_id

    async def edit_message(self, chat_id: str, message_id: str, new_content: str) -> bool:
        """编辑已发送的消息."""
        result = await self._api_request(
            "PUT",
            f"/im/v1/messages/{message_id}",
            json_data={
                "msg_type": "text",
                "content": json.dumps({"text": new_content}, ensure_ascii=False),
            },
        )
        return result.get("code") == 0

    async def delete_message(self, chat_id: str, message_id: str) -> bool:
        """撤回消息."""
        result = await self._api_request("DELETE", f"/im/v1/messages/{message_id}")
        return result.get("code") == 0

    async def _upload_audio(self, audio_data: bytes) -> str:
        """上传音频文件到飞书, 返回 file_key.

        飞书要求 opus 格式，先转换再上传。
        """
        from gateway.voice.audio_utils import convert_audio

        # 转换为 opus (飞书语音消息格式)
        opus_data = await convert_audio(audio_data, from_format="auto", to_format="opus")

        client = await self._ensure_http_client()
        token = await self._get_tenant_token()

        import io
        resp = await client.post(
            "https://open.feishu.cn/open-apis/im/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            data={"file_type": "opus", "file_name": "voice.opus"},
            files={"file": ("voice.opus", io.BytesIO(opus_data), "audio/ogg")},
        )
        result = resp.json()
        if result.get("code") != 0:
            raise RuntimeError(f"飞书音频上传失败: {result.get('msg', '')}")
        return result["data"]["file_key"]

    # ── 安全: 签名验证 + 加密解密 + 事件去重 ──────────────

    def _verify_signature(self, timestamp: str, nonce: str, body: str) -> bool:
        """验证飞书 Webhook 签名 (encrypt_key 未配置时跳过)."""
        if not self._encrypt_key:
            return True
        payload = timestamp + nonce + self._encrypt_key + body
        expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return hmac.compare_digest(expected, "")  # placeholder, see header check below

    def _decrypt_event(self, encrypted: str) -> dict:
        """AES-256-CBC 解密飞书加密事件体."""
        key = hashlib.sha256(self._encrypt_key.encode("utf-8")).digest()
        encrypted_bytes = base64.b64decode(encrypted)
        iv = encrypted_bytes[:16]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(encrypted_bytes[16:]) + decryptor.finalize()
        # PKCS7 unpadding
        pad_len = decrypted[-1]
        decrypted = decrypted[:-pad_len]
        return json.loads(decrypted.decode("utf-8"))

    def _check_dedup(self, event_id: str) -> bool:
        """检查事件是否已处理过 (返回 True 表示重复)."""
        now = time.time()
        # 清理过期条目
        expired = [k for k, t in self._processed_events.items() if now - t > self._dedup_ttl]
        for k in expired:
            del self._processed_events[k]
        # 检查
        if event_id in self._processed_events:
            return True
        self._processed_events[event_id] = now
        return False

    # ── Webhook 处理 ──────────────────────────────────────────

    async def _handle_webhook(self, request: Any) -> Any:
        """处理飞书 Webhook 回调 (含签名验证+去重+解密)."""
        from aiohttp import web

        try:
            raw_body = await request.text()
            body = json.loads(raw_body)
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        # 签名验证
        if self._encrypt_key:
            timestamp = request.headers.get("X-Lark-Request-Timestamp", "")
            nonce = request.headers.get("X-Lark-Request-Nonce", "")
            signature = request.headers.get("X-Lark-Signature", "")
            payload = timestamp + nonce + self._encrypt_key + raw_body
            expected_sig = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(expected_sig, signature):
                logger.warning("飞书 Webhook 签名验证失败")
                return web.json_response({"error": "invalid signature"}, status=403)

        # 加密事件体解密
        if "encrypt" in body and self._encrypt_key:
            try:
                body = self._decrypt_event(body["encrypt"])
            except Exception as e:
                logger.error("飞书事件解密失败: %s", e)
                return web.json_response({"error": "decrypt failed"}, status=400)

        # URL 验证
        if body.get("type") == "url_verification":
            challenge = body.get("challenge", "")
            return web.json_response({"challenge": challenge})

        # 事件去重
        header = body.get("header", {})
        event_id = header.get("event_id", "")
        if event_id and self._check_dedup(event_id):
            logger.debug("飞书事件去重: %s", event_id)
            return web.json_response({"ok": True})

        event = body.get("event", {})
        if not event:
            return web.json_response({"ok": True})

        event_type = header.get("event_type", "")

        if event_type == "im.message.receive_v1":
            await self._handle_message_event(event)
        else:
            logger.debug("飞书未处理事件: %s", event_type)

        return web.json_response({"ok": True})

    async def _handle_message_event(self, event: dict) -> None:
        """处理消息事件."""
        msg = event.get("message", {})
        sender_info = event.get("sender", {}).get("sender_id", {})

        # 解析发送者
        sender = PlatformUser(
            user_id=sender_info.get("open_id", ""),
            username=sender_info.get("user_id", ""),
            display_name="",  # 需要额外 API 获取
        )

        # 解析聊天
        chat_type_str = msg.get("chat_type", "p2p")
        chat_type = ChatType.PRIVATE if chat_type_str == "p2p" else ChatType.GROUP

        chat = PlatformChat(
            chat_id=msg.get("chat_id", ""),
            chat_type=chat_type,
            platform=PlatformType.FEISHU,
        )

        # 解析消息内容
        msg_type = msg.get("message_type", "text")
        content_str = msg.get("content", "{}")
        try:
            content_data = json.loads(content_str)
        except json.JSONDecodeError:
            content_data = {"text": content_str}

        message_type = MessageType.TEXT
        content = ""
        media_url = ""

        if msg_type == "text":
            content = content_data.get("text", "")
        elif msg_type == "image":
            message_type = MessageType.IMAGE
            media_url = content_data.get("image_key", "")
        elif msg_type == "file":
            message_type = MessageType.FILE
            media_url = content_data.get("file_key", "")
        elif msg_type == "audio":
            message_type = MessageType.VOICE
            media_url = content_data.get("file_key", "")
        elif msg_type == "post":
            message_type = MessageType.RICH_TEXT
            # 解析富文本
            post = content_data.get("zh_cn", content_data.get("en_us", {}))
            content = post.get("title", "")
            for paragraph in post.get("content", []):
                for element in paragraph:
                    if element.get("tag") == "text":
                        content += element.get("text", "")
        else:
            content = f"[{msg_type}]"

        # 检查群聊中是否 @了机器人
        mentions = []
        if msg.get("mentions"):
            for mention in msg["mentions"]:
                mentions.append(mention.get("id", {}).get("open_id", ""))
                # 去掉 @机器人 的文本
                mention_name = mention.get("name", "")
                if mention_name:
                    content = content.replace(f"@{mention_name}", "").strip()

        platform_msg = PlatformMessage(
            message_id=msg.get("message_id", ""),
            platform=PlatformType.FEISHU,
            chat=chat,
            sender=sender,
            message_type=message_type,
            content=content,
            media_url=media_url,
            reply_to_id=msg.get("parent_id"),
            mentions=mentions,
            timestamp=time.time(),
            raw=event,
        )

        # 语音消息: 下载音频数据
        if message_type == MessageType.VOICE and media_url:
            try:
                audio_bytes = await self._download_resource(
                    msg.get("message_id", ""), media_url, "file",
                )
                platform_msg.media_data = audio_bytes
            except Exception as e:
                logger.error("飞书语音下载失败: %s", e)

        # 群聊中需要 @机器人
        if chat_type == ChatType.GROUP:
            if self._bot_user and self._bot_user.user_id not in mentions:
                return

        await self._dispatch_message(platform_msg)
