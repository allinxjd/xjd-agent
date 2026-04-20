"""Redis 集成 — 消息队列 + 分布式会话 + 缓存.

提供:
- 可靠消息队列 (Redis Streams)
- 分布式会话存储
- 模型响应缓存
- 限流器 (Rate Limiter)
- Pub/Sub 事件广播
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

@dataclass
class QueueMessage:
    """队列消息."""

    message_id: str = ""
    stream: str = ""
    data: dict[str, Any] = None  # type: ignore
    timestamp: float = 0.0

    def __post_init__(self):
        if self.data is None:
            self.data = {}

class RedisManager:
    """Redis 管理器.

    用法:
        redis = RedisManager(url="redis://localhost:6379")
        await redis.initialize()

        # 消息队列
        await redis.enqueue("tasks", {"type": "chat", "message": "hello"})
        msg = await redis.dequeue("tasks")

        # 会话缓存
        await redis.session_set("user:123", {"messages": [...]})
        data = await redis.session_get("user:123")

        # 限流
        allowed = await redis.rate_limit("api:user:123", max_requests=10, window=60)
    """

    def __init__(
        self,
        url: str = "redis://localhost:6379",
        db: int = 0,
        key_prefix: str = "xjd:",
        pool_size: int = 10,
    ) -> None:
        self._url = url
        self._db = db
        self._prefix = key_prefix
        self._pool_size = pool_size
        self._redis = None
        self._pubsub = None
        self._subscribers: dict[str, list[Callable]] = {}

    async def initialize(self) -> None:
        """初始化 Redis 连接."""
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                self._url,
                db=self._db,
                decode_responses=True,
                max_connections=self._pool_size,
            )

            # 测试连接
            await self._redis.ping()
            logger.info("Redis connected: %s", self._url)

        except ImportError:
            raise ImportError("redis 未安装。请运行: pip install redis[hiredis]")
        except Exception as e:
            logger.error("Redis connection failed: %s", e)
            raise

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    # ─── 消息队列 (Redis Streams) ───

    async def enqueue(
        self,
        stream: str,
        data: dict[str, Any],
        max_len: int = 10000,
    ) -> str:
        """发送消息到队列."""
        if not self._redis:
            raise RuntimeError("Redis not initialized")

        # 序列化值
        flat = {k: json.dumps(v) if not isinstance(v, str) else v for k, v in data.items()}
        flat["_ts"] = str(time.time())

        msg_id = await self._redis.xadd(
            self._key(f"stream:{stream}"),
            flat,
            maxlen=max_len,
        )
        return msg_id

    async def dequeue(
        self,
        stream: str,
        group: str = "default",
        consumer: str = "worker-1",
        block: int = 5000,
        count: int = 1,
    ) -> list[QueueMessage]:
        """从队列消费消息 (Consumer Group)."""
        if not self._redis:
            raise RuntimeError("Redis not initialized")

        key = self._key(f"stream:{stream}")

        # 确保 Consumer Group 存在
        try:
            await self._redis.xgroup_create(key, group, id="0", mkstream=True)
        except Exception:
            pass  # Group 已存在

        results = await self._redis.xreadgroup(
            group, consumer,
            {key: ">"},
            count=count,
            block=block,
        )

        messages = []
        for stream_name, entries in results:
            for msg_id, fields in entries:
                data = {}
                for k, v in fields.items():
                    if k == "_ts":
                        continue
                    try:
                        data[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        data[k] = v

                messages.append(QueueMessage(
                    message_id=msg_id,
                    stream=stream,
                    data=data,
                    timestamp=float(fields.get("_ts", 0)),
                ))

        return messages

    async def ack(self, stream: str, group: str, message_id: str) -> None:
        """确认消息已处理."""
        if self._redis:
            await self._redis.xack(self._key(f"stream:{stream}"), group, message_id)

    # ─── 会话缓存 ───

    async def session_set(
        self,
        session_id: str,
        data: dict[str, Any],
        ttl: int = 86400,
    ) -> None:
        """保存会话数据."""
        if not self._redis:
            return
        key = self._key(f"session:{session_id}")
        await self._redis.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)

    async def session_get(self, session_id: str) -> Optional[dict[str, Any]]:
        """获取会话数据."""
        if not self._redis:
            return None
        key = self._key(f"session:{session_id}")
        data = await self._redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def session_delete(self, session_id: str) -> None:
        """删除会话."""
        if self._redis:
            await self._redis.delete(self._key(f"session:{session_id}"))

    async def session_extend(self, session_id: str, ttl: int = 86400) -> None:
        """延长会话 TTL."""
        if self._redis:
            await self._redis.expire(self._key(f"session:{session_id}"), ttl)

    # ─── 缓存 ───

    async def cache_set(
        self,
        key: str,
        value: Any,
        ttl: int = 300,
    ) -> None:
        """设置缓存."""
        if self._redis:
            await self._redis.set(
                self._key(f"cache:{key}"),
                json.dumps(value, ensure_ascii=False),
                ex=ttl,
            )

    async def cache_get(self, key: str) -> Any:
        """获取缓存."""
        if not self._redis:
            return None
        data = await self._redis.get(self._key(f"cache:{key}"))
        if data:
            return json.loads(data)
        return None

    async def cache_delete(self, key: str) -> None:
        if self._redis:
            await self._redis.delete(self._key(f"cache:{key}"))

    # ─── 限流 (滑动窗口) ───

    async def rate_limit(
        self,
        identifier: str,
        max_requests: int = 10,
        window: int = 60,
    ) -> bool:
        """检查限流.

        Returns:
            True = 允许, False = 已超限
        """
        if not self._redis:
            return True

        key = self._key(f"ratelimit:{identifier}")
        now = time.time()
        window_start = now - window

        pipe = self._redis.pipeline()
        # 移除过期记录
        pipe.zremrangebyscore(key, 0, window_start)
        # 统计当前窗口请求数
        pipe.zcard(key)
        # 添加当前请求
        pipe.zadd(key, {str(now): now})
        # 设置 TTL
        pipe.expire(key, window + 1)

        results = await pipe.execute()
        current_count = results[1]

        return current_count < max_requests

    async def rate_limit_info(self, identifier: str, window: int = 60) -> dict:
        """获取限流信息."""
        if not self._redis:
            return {"remaining": -1}

        key = self._key(f"ratelimit:{identifier}")
        now = time.time()
        window_start = now - window

        await self._redis.zremrangebyscore(key, 0, window_start)
        count = await self._redis.zcard(key)

        return {
            "current": count,
            "window": window,
        }

    # ─── Pub/Sub ───

    async def publish(self, channel: str, data: dict[str, Any]) -> None:
        """发布事件."""
        if self._redis:
            await self._redis.publish(
                self._key(f"pubsub:{channel}"),
                json.dumps(data, ensure_ascii=False),
            )

    async def subscribe(
        self,
        channel: str,
        callback: Callable[[dict], Coroutine],
    ) -> None:
        """订阅事件."""
        if not self._redis:
            return

        if self._pubsub is None:
            self._pubsub = self._redis.pubsub()

        key = self._key(f"pubsub:{channel}")
        await self._pubsub.subscribe(key)

        if channel not in self._subscribers:
            self._subscribers[channel] = []
        self._subscribers[channel].append(callback)

        # 启动监听
        asyncio.create_task(self._listen_pubsub())

    async def _listen_pubsub(self) -> None:
        """监听 Pub/Sub."""
        if not self._pubsub:
            return

        try:
            async for message in self._pubsub.listen():
                if message["type"] != "message":
                    continue

                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()

                # 去掉前缀
                short_channel = channel.replace(self._prefix + "pubsub:", "")

                try:
                    data = json.loads(message["data"])
                except (json.JSONDecodeError, TypeError):
                    data = {"raw": message["data"]}

                for cb in self._subscribers.get(short_channel, []):
                    try:
                        await cb(data)
                    except Exception as e:
                        logger.error("Pub/Sub callback error: %s", e)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Pub/Sub listener error: %s", e)

    # ─── 通用 ───

    async def health_check(self) -> bool:
        """健康检查."""
        if not self._redis:
            return False
        try:
            return await self._redis.ping()
        except Exception:
            return False

    async def close(self) -> None:
        """关闭连接."""
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.close()
            self._pubsub = None
        if self._redis:
            await self._redis.close()
            self._redis = None
        logger.info("Redis connection closed")
