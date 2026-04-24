"""Gateway WebSocket 控制面 — 统一管理所有消息渠道的连接、消息路由和会话管理。
Agent 通过 WebSocket 连接到 Gateway，实现跨平台消息互通。

架构:
    ┌─────────────────────────────────────────────────┐
    │                 Gateway Server                   │
    │  ws://0.0.0.0:18789                              │
    │                                                  │
    │  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
    │  │ Telegram  │  │  飞书    │  │  钉钉     │  ... │
    │  │ Adapter   │  │ Adapter  │  │ Adapter   │      │
    │  └─────┬─────┘  └─────┬────┘  └─────┬────┘      │
    │        │              │              │            │
    │  ┌─────▼──────────────▼──────────────▼─────┐     │
    │  │         Session Manager                  │     │
    │  │    (per-user conversation state)         │     │
    │  └──────────────────┬──────────────────────┘     │
    │                     │                            │
    │  ┌──────────────────▼──────────────────────┐     │
    │  │         Agent Engine                     │     │
    │  │    (model routing + tool calling)        │     │
    │  └─────────────────────────────────────────┘     │
    └─────────────────────────────────────────────────┘

关键设计:
    - 每个用户有独立的 Session (跨平台共享)
    - 同一用户在不同平台的消息会合并到同一个对话上下文
    - 支持 DM 配对 (pairing) 和开放 (open) 两种策略
    - 消息队列确保有序处理，避免并发冲突
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Optional

from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageType,
    PlatformMessage,
    PlatformEvent,
    PlatformType,
    OutgoingMessage,
    EventType,
)

logger = logging.getLogger(__name__)

@dataclass
class GatewayStats:
    """网关统计."""

    started_at: float = 0.0
    total_messages_received: int = 0
    total_messages_sent: int = 0
    total_sessions_created: int = 0
    active_sessions: int = 0
    platform_stats: dict[str, dict[str, int]] = field(default_factory=dict)

class GatewayServer:
    """Gateway 核心服务器.

    用法:
        gateway = GatewayServer(agent_engine, config)

        # 注册平台适配器
        gateway.register_adapter(TelegramAdapter({...}))
        gateway.register_adapter(FeishuAdapter({...}))

        # 启动
        await gateway.start()

        # 停止
        await gateway.stop()
    """

    def __init__(
        self,
        agent_engine: Any,  # AgentEngine
        config: dict[str, Any] | None = None,
        inspector_callback: Any = None,
    ) -> None:
        from gateway.core.session import SessionManager

        self._engine = agent_engine
        self._config = config or {}
        self._inspector_callback = inspector_callback

        # 从 engine 获取 SkillManager (如果有)
        self._skill_manager = getattr(agent_engine, '_skill_manager', None)

        # 语音管线 (延迟初始化)
        self._voice_pipeline: Any = None
        self._voice_enabled = False

        # 电商协调器 (延迟初始化)
        self._ecommerce_coordinator: Any = None
        self._ecommerce_mode = self._config.get("ecommerce_mode", False)

        # 平台适配器
        self._adapters: dict[str, BasePlatformAdapter] = {}

        # 会话管理
        self._session_manager = SessionManager(
            dm_policy=self._config.get("dm_policy", "pairing"),
        )

        # 消息处理队列 (确保每个 session 串行处理)
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._session_lock_last_used: dict[str, float] = {}

        # Engine 并发锁 — 共享 engine 同一时刻只能处理一个请求
        self._engine_lock = asyncio.Lock()

        # 定期清理任务
        self._cleanup_task: Optional[asyncio.Task] = None

        # 统计
        self._stats = GatewayStats()

        # WebSocket 服务器
        self._ws_server = None
        self._ws_connections: set[Any] = set()

        # 运行状态
        self._running = False

        # 定时调度器 + 主动通知器 (start() 中初始化)
        self._scheduler: Any = None
        self._notifier: Any = None

    @property
    def stats(self) -> GatewayStats:
        return self._stats

    def _emit_inspector(self, event: dict) -> None:
        if self._inspector_callback:
            try:
                asyncio.get_event_loop().create_task(self._inspector_callback(event))
            except Exception:
                logger.debug("Inspector emit failed: %s", event.get("event_type", "?"), exc_info=True)

    # ── 适配器管理 ────────────────────────────────────────────

    def register_adapter(self, adapter: BasePlatformAdapter) -> None:
        """注册消息平台适配器."""
        name = adapter.platform_type.value
        self._adapters[name] = adapter

        # 绑定消息处理器
        adapter.on_message(self._handle_incoming_message)
        adapter.on_event(self._handle_platform_event)

        logger.info("Gateway: registered adapter %s", name)

    def get_adapter(self, platform: str) -> Optional[BasePlatformAdapter]:
        """获取平台适配器."""
        return self._adapters.get(platform)

    async def add_adapter_runtime(self, platform: str, config: dict[str, Any]) -> str:
        """运行时动态添加并启动适配器.

        Returns:
            状态信息 ("ok" / 错误描述)
        """
        from gateway.platforms.schemas import ADAPTER_MAP
        import importlib

        entry = ADAPTER_MAP.get(platform)
        if not entry:
            return f"不支持的平台: {platform}"

        module_path, class_name = entry
        try:
            mod = importlib.import_module(module_path)
            adapter_cls = getattr(mod, class_name)
        except (ImportError, AttributeError) as e:
            return f"适配器加载失败: {e}"

        # 如果已存在，先停止
        if platform in self._adapters:
            await self.remove_adapter_runtime(platform)

        adapter = adapter_cls(config)
        self.register_adapter(adapter)

        try:
            await adapter.start()
            logger.info("Gateway: adapter %s started at runtime", platform)
            return "ok"
        except Exception as e:
            logger.error("Gateway: adapter %s runtime start failed: %s", platform, e)
            return str(e)

    async def remove_adapter_runtime(self, platform: str) -> None:
        """运行时停止并移除适配器."""
        adapter = self._adapters.pop(platform, None)
        if adapter and adapter.is_running:
            await adapter.stop()
            logger.info("Gateway: adapter %s removed at runtime", platform)

    async def restart_adapter(self, platform: str) -> str:
        """重启适配器 (stop → start)."""
        adapter = self._adapters.get(platform)
        if not adapter:
            return f"适配器 {platform} 不存在"
        try:
            if adapter.is_running:
                await adapter.stop()
            await adapter.start()
            return "ok"
        except Exception as e:
            return str(e)

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        """启动 Gateway — 启动所有适配器 + WebSocket 服务器."""
        self._stats.started_at = time.time()
        self._running = True

        # 初始化语音管线
        await self._init_voice_pipeline()

        # 初始化电商协调器
        if self._ecommerce_mode:
            self._init_ecommerce_coordinator()

        # 启动所有适配器
        start_tasks = []
        for name, adapter in self._adapters.items():
            logger.info("Gateway: starting adapter %s...", name)
            start_tasks.append(self._start_adapter(name, adapter))

        if start_tasks:
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            for name, result in zip(self._adapters.keys(), results):
                if isinstance(result, Exception):
                    logger.error("Gateway: adapter %s failed to start: %s", name, result)
                else:
                    logger.info("Gateway: adapter %s started", name)

        # 启动 WebSocket 服务器
        ws_host = self._config.get("host", "127.0.0.1")
        ws_port = self._config.get("port", 18789)
        try:
            import websockets
            self._ws_server = await websockets.serve(  # type: ignore
                self._handle_ws_connection,
                ws_host,
                ws_port,
            )
            logger.info("Gateway WebSocket server started: ws://%s:%d", ws_host, ws_port)
        except ImportError:
            logger.warning("websockets 未安装，WebSocket 控制面不可用。pip install websockets")
        except Exception as e:
            logger.error("WebSocket server failed: %s", e)

        logger.info(
            "Gateway 已启动: %d 个平台适配器",
            sum(1 for a in self._adapters.values() if a.is_running),
        )

        # 启动定时调度器
        try:
            from gateway.cron.scheduler import CronScheduler
            self._scheduler = CronScheduler()
            await self._scheduler.initialize()
            self._scheduler.set_executor(self._execute_cron_task)
            await self._scheduler.start()
            logger.info("CronScheduler 已启动")
        except Exception as e:
            logger.warning("CronScheduler 初始化失败: %s", e)

        # 启动主动通知器
        try:
            from gateway.core.proactive import ProactiveNotifier
            self._notifier = ProactiveNotifier()
            for name, adapter in self._adapters.items():
                self._notifier.register_channel(name, self._make_send_fn(adapter))
        except Exception as e:
            logger.warning("ProactiveNotifier 初始化失败: %s", e)

        # 启动定期清理任务 (session locks + expired sessions)
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _start_adapter(self, name: str, adapter: BasePlatformAdapter, max_retries: int = 3) -> None:
        """安全启动单个适配器 (带重试)."""
        for attempt in range(1, max_retries + 1):
            try:
                await asyncio.wait_for(adapter.start(), timeout=30.0)
                return
            except asyncio.TimeoutError:
                logger.error("Adapter %s start timed out (attempt %d/%d)", name, attempt, max_retries)
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error("Adapter %s start failed (attempt %d/%d): %s", name, attempt, max_retries, e)
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)  # 指数退避: 2s, 4s
                else:
                    raise

    async def _periodic_cleanup(self) -> None:
        """定期清理过期资源 — session locks、expired sessions."""
        while self._running:
            try:
                await asyncio.sleep(300)  # 每 5 分钟
                now = time.time()

                # 清理闲置超过 30 分钟且未被持有的 session locks
                stale_keys = [
                    k for k, t in self._session_lock_last_used.items()
                    if now - t > 1800 and k in self._session_locks and not self._session_locks[k].locked()
                ]
                for k in stale_keys:
                    self._session_locks.pop(k, None)
                    self._session_lock_last_used.pop(k, None)
                if stale_keys:
                    logger.debug("Cleaned up %d stale session locks", len(stale_keys))

                # 清理过期 sessions
                cleaned = await self._session_manager.cleanup_expired()
                if cleaned:
                    logger.debug("Cleaned up %d expired sessions", cleaned)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Periodic cleanup error: %s", e)

    async def stop(self) -> None:
        """停止 Gateway."""
        self._running = False

        # 停止定期清理
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 停止 WebSocket 服务器
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

        # 停止定时调度器
        if self._scheduler:
            try:
                await self._scheduler.stop()
                await self._scheduler.close()
            except Exception as e:
                logger.warning("CronScheduler 关闭失败: %s", e)

        # 停止所有适配器 (无论 is_running 状态，确保后台 task 被 cancel)
        stop_tasks = []
        for name, adapter in self._adapters.items():
            stop_tasks.append(adapter.stop())

        if stop_tasks:
            await asyncio.gather(*stop_tasks, return_exceptions=True)

        # 保存会话状态
        await self._session_manager.save_all()

        logger.info("Gateway 已停止")

    # ── 语音管线 ──────────────────────────────────────────────

    async def _init_voice_pipeline(self) -> None:
        """初始化语音管线 (如果配置了)."""
        voice_config = self._config.get("voice")
        if not voice_config or not voice_config.get("enabled"):
            return

        try:
            from gateway.voice.pipeline import VoicePipeline
            from gateway.voice.audio_utils import check_ffmpeg

            if not await check_ffmpeg():
                logger.warning("ffmpeg 未安装，语音管线不可用")
                return

            self._voice_pipeline = VoicePipeline(voice_config)
            await self._voice_pipeline.initialize()
            self._voice_enabled = True
            logger.info("语音管线已初始化: STT=%s, TTS=%s",
                        voice_config.get("stt_provider", "whisper_local"),
                        voice_config.get("tts_provider", "edge_tts"))
        except Exception as e:
            logger.error("语音管线初始化失败: %s", e, exc_info=True)

    def _init_ecommerce_coordinator(self) -> None:
        """初始化电商协调器."""
        try:
            from agent.ecommerce.coordinator import ECommerceCoordinator
            from agent.ecommerce.tools import register_ecommerce_tools

            # 注册电商工具
            if hasattr(self._engine, '_registry') and self._engine._registry:
                register_ecommerce_tools(self._engine._registry)

            redis = self._config.get("redis")
            self._ecommerce_coordinator = ECommerceCoordinator(
                router=self._engine._router if hasattr(self._engine, '_router') else None,
                tool_registry=self._engine._registry if hasattr(self._engine, '_registry') else None,
                redis=redis,
            )
            logger.info("电商协调器已初始化")
        except Exception as e:
            logger.error("电商协调器初始化失败: %s", e, exc_info=True)
            self._ecommerce_mode = False

    async def _process_voice_message(self, session: Any, message: PlatformMessage) -> tuple[str, bytes]:
        """处理语音消息: STT → Engine → TTS.

        Returns:
            (reply_text, audio_data) — audio_data 为空时降级为文本回复
        """
        from gateway.voice.audio_utils import convert_audio

        # 1. 音频格式转换 → wav (Whisper 需要)
        wav_data = await convert_audio(
            message.media_data, from_format="auto", to_format="wav",
            sample_rate=16000, channels=1,
        )

        # 2. STT: 语音 → 文本
        transcribed = await self._voice_pipeline.speech_to_text(wav_data)
        if not transcribed.strip():
            return "抱歉，没有识别到语音内容。", b""

        logger.info("STT 转写: %s", transcribed[:100])

        # 3. Agent Engine 处理文本
        # 临时替换 message.content 为转写文本
        message.content = transcribed
        reply_text = await self._process_with_engine(session, message)

        # 4. TTS: 文本 → 语音
        try:
            audio_data = await self._voice_pipeline.text_to_speech(reply_text)
            return reply_text, audio_data
        except Exception as e:
            logger.warning("TTS 合成失败，降级为文本: %s", e)
            return reply_text, b""

    # ── 定时任务执行 ────────────────────────────────────────────

    async def _execute_cron_task(self, task: Any) -> None:
        """执行定时任务：调用 engine 处理 prompt，将结果发送到指定平台."""
        self._emit_inspector({
            "event_type": "cron_start",
            "title": f"Cron: {getattr(task, 'name', 'task')}",
            "detail": f"platform={getattr(task, 'platform', '')}, prompt={getattr(task, 'prompt', '')[:80]}",
            "timestamp": time.time(),
        })
        _cron_start = time.time()

        prompt = task.prompt
        if task.platform and task.chat_id:
            prompt = (
                f"[定时任务 | 平台: {task.platform} | 目标: {task.chat_id}]\n"
                f"注意：你的回复会自动发送到目标渠道，不需要调用 send_to_contact 或 list_contacts 等工具来发送。"
                f"直接输出最终内容即可，不要包含任何工具调用说明、推送状态或内部提示。\n\n"
                f"{prompt}"
            )

        async with self._engine_lock:
            result = await self._engine.run_turn(
                prompt,
                skill_id=getattr(task, 'skill_id', '') or None,
            )
        reply = result.content
        self._emit_inspector({
            "event_type": "cron_complete",
            "title": f"Cron Done: {getattr(task, 'name', 'task')}",
            "detail": f"reply_len={len(reply or '')}, tokens={result.total_usage.total_tokens}",
            "timestamp": time.time(),
            "duration_ms": round((time.time() - _cron_start) * 1000),
        })
        if not reply:
            return

        if task.platform and task.chat_id:
            adapter = self._adapters.get(task.platform)
            if adapter and adapter.is_running:
                try:
                    await adapter.send_text(task.chat_id, reply)
                except Exception as e:
                    logger.error("Cron task send failed (%s): %s", task.platform, e)
            elif self._notifier:
                await self._notifier.send_direct(task.platform, task.chat_id, reply)

    def _make_send_fn(self, adapter: BasePlatformAdapter):
        async def _send(channel: str, recipient: str, message: str) -> bool:
            try:
                await adapter.send_text(recipient, message)
                return True
            except Exception as e:
                logger.error("Notifier send failed (%s): %s", channel, e)
                return False
        return _send

    # ── 消息处理 ──────────────────────────────────────────────

    async def _handle_incoming_message(self, message: PlatformMessage) -> None:
        """处理收到的消息 — 所有平台消息的入口.

        流程:
        1. 查找/创建 Session
        2. 获取 Session 锁 (串行处理)
        3. 调用 AgentEngine 处理
        4. 将回复发送到对应平台
        """
        self._stats.total_messages_received += 1
        platform = message.platform.value
        self._stats.platform_stats.setdefault(platform, {"received": 0, "sent": 0})
        self._stats.platform_stats[platform]["received"] += 1

        self._emit_inspector({
            "event_type": "message_in",
            "title": f"GW In ({platform})",
            "detail": f"user={message.sender.user_id}, {message.content[:80]}",
            "timestamp": time.time(),
        })

        # 获取/创建 Session
        session = await self._session_manager.get_or_create(
            user_id=message.sender.user_id,
            platform=message.platform,
            chat_id=message.chat.chat_id,
        )

        # 获取 session 级别的锁 (确保同一用户的消息串行处理)
        session_key = session.session_id
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        self._session_lock_last_used[session_key] = time.time()

        async with lock:
            try:
                reply_text = ""
                audio_data = b""

                # 发送"正在输入"状态
                adapter = self._adapters.get(platform)
                if adapter and adapter.is_running:
                    try:
                        await adapter.send_typing(message.chat.chat_id, "start")
                    except Exception:
                        pass

                # 语音消息走语音管线
                if message.message_type == MessageType.VOICE and self._voice_enabled:
                    reply_text, audio_data = await self._process_voice_message(session, message)
                else:
                    # 媒体消息落盘到本地 inbox
                    if message.media_data and message.message_type in (
                        MessageType.IMAGE, MessageType.FILE, MessageType.VIDEO,
                    ):
                        try:
                            from gateway.media.channel_files import get_channel_file_manager
                            cfm = get_channel_file_manager()
                            saved_path = cfm.save_incoming(
                                message.media_data, message.message_type.value, message.platform.value,
                            )
                            message.metadata["local_file_path"] = saved_path
                        except Exception as e:
                            logger.warning("媒体文件落盘失败: %s", e)

                    # 文本/其他消息走 Agent Engine
                    reply_text = await self._process_with_engine(session, message)

                if reply_text:
                    adapter = self._adapters.get(platform)
                    if adapter and adapter.is_running:
                        if audio_data:
                            # 语音回复 (带文本 fallback)
                            try:
                                await asyncio.wait_for(adapter.send_voice(
                                    chat_id=message.chat.chat_id,
                                    audio_data=audio_data,
                                    fallback_text=reply_text,
                                ), timeout=30.0)
                            except (asyncio.TimeoutError, Exception) as e:
                                logger.error("Adapter send_voice failed: %s", e)
                        else:
                            try:
                                await asyncio.wait_for(adapter.send_text(
                                    chat_id=message.chat.chat_id,
                                    text=reply_text,
                                    reply_to=message.message_id,
                                ), timeout=30.0)
                            except (asyncio.TimeoutError, Exception) as e:
                                logger.error("Adapter send_text failed: %s", e)
                        self._stats.total_messages_sent += 1
                        self._stats.platform_stats[platform]["sent"] += 1
                        self._emit_inspector({
                            "event_type": "message_out",
                            "title": f"GW Out ({platform})",
                            "detail": f"chat={message.chat.chat_id}, len={len(reply_text)}",
                            "timestamp": time.time(),
                        })

                # 广播到 WebSocket 客户端
                await self._broadcast_ws({
                    "type": "message",
                    "session_id": session_key,
                    "platform": platform,
                    "user_id": message.sender.user_id,
                    "content": message.content,
                    "reply": reply_text,
                    "timestamp": time.time(),
                })

            except Exception as e:
                logger.error(
                    "Gateway message handling error (session=%s): %s",
                    session_key, e, exc_info=True,
                )
                self._emit_inspector({
                    "event_type": "error",
                    "title": f"GW Error ({platform})",
                    "detail": str(e)[:200],
                    "timestamp": time.time(),
                })
                # 发送错误提示
                adapter = self._adapters.get(platform)
                if adapter and adapter.is_running:
                    try:
                        await adapter.send_text(
                            chat_id=message.chat.chat_id,
                            text="抱歉，处理消息时出现错误。请稍后重试。",
                        )
                    except Exception:
                        pass

    async def _process_with_engine(self, session: Any, message: PlatformMessage) -> str:
        """使用 Agent Engine 处理消息 — session 级别的上下文管理."""
        from agent.providers.base import Message
        from agent.context_engine.manager import ContextEngine

        # 从 session 构建独立消息历史，用 ContextEngine 智能压缩
        all_msgs: list[Message] = []
        for msg in session.messages:
            all_msgs.append(
                Message(role=msg.get("role", "user"), content=msg.get("content", ""))
            )
        ctx_engine = ContextEngine(max_context_tokens=self._engine._max_context_tokens)
        if ctx_engine.should_auto_compact(all_msgs):
            try:
                all_msgs = await ctx_engine.manage(all_msgs, self._engine._router)
            except Exception as _ce:
                logger.warning("Context auto-compact failed: %s", _ce)
                all_msgs = all_msgs[-50:]
        session_msgs = all_msgs

        # 媒体文件路径注入
        local_path = message.metadata.get("local_file_path", "")
        user_text_with_file = message.content
        if local_path:
            user_text_with_file += f"\n[用户发送的文件已保存到本地: {local_path}]"

        # 电商模式: 电商意图走协调器，做图/非电商请求交给主引擎
        if self._ecommerce_mode and self._ecommerce_coordinator:
            intents = self._ecommerce_coordinator._classify_intent(message.content)
            if "image_generation" not in intents and "non_ecommerce" not in intents:
                reply = await self._ecommerce_coordinator.handle_message(
                    user_text_with_file,
                    session_id=session.session_id,
                )
                session.add_message("user", user_text_with_file)
                session.add_message("assistant", reply)
                await self._session_manager._persist_session(session)
                return reply
            else:
                logger.info("非电商协调器意图 %s，交给主引擎: %s", intents, message.content[:50])

        # 构建平台上下文前缀
        platform_name = message.platform.value
        chat_type = message.chat.chat_type.value if hasattr(message.chat, 'chat_type') else "private"
        sender_name = message.sender.display_name or message.sender.username or message.sender.user_id
        platform_ctx = f"[来源: {platform_name} | 会话类型: {chat_type} | 发送者: {sender_name}]"
        user_content = f"{platform_ctx}\n{user_text_with_file}"

        # 记录完整用户消息到 session（含文件路径注入）
        session.add_message("user", user_content)

        # Canvas 跨平台广播回调 — 飞书/微信触发的 canvas 推送到 WebUI
        def on_tool_result(name: str, result: str):
            if name in ("create_canvas", "update_canvas") and result and '"__canvas_render__"' in result:
                try:
                    canvas_data = json.loads(result)
                    if canvas_data.get("__canvas_render__"):
                        web_server = getattr(self, '_web_server', None)
                        if web_server:
                            asyncio.ensure_future(
                                web_server.broadcast_canvas(canvas_data, platform_name, sender_name)
                            )
                except Exception:
                    logger.debug("Canvas broadcast failed", exc_info=True)
            if name == "export_canvas" and result and '"__canvas_export__"' in result:
                try:
                    export_data = json.loads(result)
                    if export_data.get("__canvas_export__"):
                        import base64
                        file_bytes = base64.b64decode(export_data["file_data"])
                        filename = export_data.get("filename", "canvas_export")
                        chat_id = message.chat.chat_id
                        adapter = self.get_adapter(platform_name)
                        if adapter:
                            asyncio.ensure_future(
                                adapter.send_file(chat_id, file_data=file_bytes, filename=filename)
                            )
                except Exception:
                    logger.debug("Canvas export delivery failed", exc_info=True)
            if name == "generate_ecommerce_image" and result:
                try:
                    data = json.loads(result)
                    if data.get("success") and data.get("images"):
                        chat_id = message.chat.chat_id
                        adapter = self.get_adapter(platform_name)
                        if adapter and adapter.capabilities.get("image"):
                            for img_info in data["images"]:
                                img_path = img_info.get("path", "")
                                if img_path:
                                    try:
                                        img_bytes = Path(img_path).read_bytes()
                                        asyncio.ensure_future(
                                            adapter.send_image(chat_id, image_data=img_bytes)
                                        )
                                    except Exception:
                                        logger.debug("Image delivery failed: %s", img_path)
                except Exception:
                    logger.debug("Ecommerce image delivery failed", exc_info=True)

        # 调用 engine（传入 session 消息，不操作全局 messages）
        result = await self._engine.run_turn(
            user_content,
            session_messages=session_msgs,
            on_tool_result=on_tool_result,
            deadline=time.time() + 300.0,
        )

        # 记录 assistant 回复到 session
        session.add_message("assistant", result.content)
        session.tool_calls_count += result.tool_calls_made
        session.total_tokens += result.total_usage.total_tokens

        # 持久化 session
        await self._session_manager._persist_session(session)

        return result.content

    async def _handle_platform_event(self, event: PlatformEvent) -> None:
        """处理平台事件 (好友请求、群变更等)."""
        logger.info(
            "Gateway event: %s from %s",
            event.event_type.value, event.platform.value,
        )

        if event.event_type == EventType.FRIEND_REQUEST:
            # 自动接受好友请求 (可配置)
            auto_accept = self._config.get("auto_accept_friend", True)
            if auto_accept:
                logger.info("Auto-accepting friend request")
                # TODO: 调用平台 API 接受好友

        # 广播事件到 WebSocket
        await self._broadcast_ws({
            "type": "event",
            "event_type": event.event_type.value,
            "platform": event.platform.value,
            "data": event.data,
            "timestamp": time.time(),
        })

    # ── WebSocket 控制面 ──────────────────────────────────────

    async def _handle_ws_connection(self, websocket: Any, path: str = "") -> None:
        """处理 WebSocket 连接 (需要 token 认证)."""
        # 认证: 检查 query param 或首条消息中的 token
        ws_token = self._config.get("ws_token", "")
        if ws_token:
            import urllib.parse
            query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
            client_token = query.get("token", [""])[0]
            if client_token != ws_token:
                await websocket.close(4001, "Unauthorized")
                return

        self._ws_connections.add(websocket)
        logger.info("WebSocket client connected: %s", websocket.remote_address)

        try:
            async for raw_msg in websocket:
                try:
                    data = json.loads(raw_msg)
                    await self._handle_ws_command(websocket, data)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({"error": "invalid JSON"}))
        except Exception:
            pass
        finally:
            self._ws_connections.discard(websocket)
            logger.info("WebSocket client disconnected")

    async def _handle_ws_command(self, websocket: Any, data: dict) -> None:
        """处理 WebSocket 命令."""
        cmd = data.get("command", "")

        if cmd == "status":
            await websocket.send(json.dumps(await self.get_status()))

        elif cmd == "sessions":
            sessions = await self._session_manager.list_sessions()
            await websocket.send(json.dumps({
                "type": "sessions",
                "data": sessions,
            }))

        elif cmd == "send":
            # 通过 WS 发送消息到指定平台
            platform = data.get("platform", "")
            chat_id = data.get("chat_id", "")
            text = data.get("text", "")
            if not platform or not chat_id or not text:
                await websocket.send(json.dumps({
                    "type": "send_result",
                    "success": False,
                    "error": "Missing required fields: platform, chat_id, text",
                }))
                return
            adapter = self._adapters.get(platform)
            if adapter and adapter.is_running:
                msg_id = await adapter.send_text(chat_id, text)
                await websocket.send(json.dumps({
                    "type": "send_result",
                    "message_id": msg_id,
                    "success": True,
                }))
            else:
                await websocket.send(json.dumps({
                    "type": "send_result",
                    "success": False,
                    "error": f"Platform {platform} not available",
                }))

        elif cmd == "adapters":
            adapters_info = {}
            for name, adapter in self._adapters.items():
                adapters_info[name] = await adapter.health_check()
            await websocket.send(json.dumps({
                "type": "adapters",
                "data": adapters_info,
            }))

        else:
            await websocket.send(json.dumps({
                "error": f"Unknown command: {cmd}",
            }))

    async def _broadcast_ws(self, data: dict) -> None:
        """广播消息到所有 WebSocket 客户端."""
        if not self._ws_connections:
            return

        payload = json.dumps(data, ensure_ascii=False)
        disconnected = set()
        for ws in self._ws_connections:
            try:
                await ws.send(payload)
            except Exception:
                disconnected.add(ws)

        self._ws_connections -= disconnected

    # ── 状态查询 ──────────────────────────────────────────────

    async def get_status(self) -> dict[str, Any]:
        """获取 Gateway 状态."""
        adapters_status = {}
        for name, adapter in self._adapters.items():
            adapters_status[name] = {
                "running": adapter.is_running,
                "capabilities": adapter.capabilities,
                "bot_user": adapter.bot_user.username if adapter.bot_user else None,
            }

        uptime = time.time() - self._stats.started_at if self._stats.started_at else 0

        return {
            "type": "status",
            "running": self._running,
            "uptime_seconds": uptime,
            "adapters": adapters_status,
            "stats": {
                "messages_received": self._stats.total_messages_received,
                "messages_sent": self._stats.total_messages_sent,
                "sessions_created": self._stats.total_sessions_created,
                "active_sessions": self._stats.active_sessions,
                "platform_stats": self._stats.platform_stats,
            },
            "ws_connections": len(self._ws_connections),
        }
