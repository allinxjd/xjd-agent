"""共享 Agent 记忆 — Redis-backed 跨 Agent 上下文共享.

Agent 写入发现/结论 → 其他 Agent 可读取，避免重复查询。
用 Pub/Sub 实时通知新写入。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

MEMORY_KEY_PREFIX = "xjd:shared_mem:"
MEMORY_TTL = 1800  # 30 分钟


class SharedMemory:
    """共享 Agent 记忆.

    用法:
        mem = SharedMemory(redis_manager)
        await mem.write("session_123", "order_status", {"order_id": "12345", "status": "shipped"}, agent_id="order-agent-1")
        data = await mem.read("session_123", "order_status")
        all_data = await mem.read_all("session_123")
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def write(
        self,
        session_id: str,
        key: str,
        value: Any,
        agent_id: str = "",
        ttl: int = MEMORY_TTL,
    ) -> None:
        """写入共享记忆."""
        r = self._redis._redis
        mem_key = f"{MEMORY_KEY_PREFIX}{session_id}"

        entry = {
            "value": json.dumps(value, ensure_ascii=False),
            "agent_id": agent_id,
            "timestamp": str(time.time()),
        }
        await r.hset(mem_key, key, json.dumps(entry, ensure_ascii=False))
        await r.expire(mem_key, ttl)

        # 通知其他 Agent
        await self._redis.publish("shared_memory", {
            "session_id": session_id,
            "key": key,
            "agent_id": agent_id,
        })

        logger.debug("SharedMemory write: %s/%s by %s", session_id, key, agent_id)

    async def read(self, session_id: str, key: str) -> Optional[Any]:
        """读取共享记忆."""
        r = self._redis._redis
        mem_key = f"{MEMORY_KEY_PREFIX}{session_id}"
        raw = await r.hget(mem_key, key)
        if not raw:
            return None
        entry = json.loads(raw)
        return json.loads(entry["value"])

    async def read_all(self, session_id: str) -> dict[str, Any]:
        """读取 session 下所有共享记忆."""
        r = self._redis._redis
        mem_key = f"{MEMORY_KEY_PREFIX}{session_id}"
        all_data = await r.hgetall(mem_key)

        result = {}
        for k, raw in all_data.items():
            try:
                entry = json.loads(raw)
                result[k] = json.loads(entry["value"])
            except (json.JSONDecodeError, KeyError):
                result[k] = raw
        return result

    async def delete(self, session_id: str, key: str) -> None:
        """删除共享记忆条目."""
        r = self._redis._redis
        await r.hdel(f"{MEMORY_KEY_PREFIX}{session_id}", key)

    async def clear(self, session_id: str) -> None:
        """清空 session 的所有共享记忆."""
        r = self._redis._redis
        await r.delete(f"{MEMORY_KEY_PREFIX}{session_id}")

    async def on_update(
        self,
        callback: Callable[[dict], Coroutine],
    ) -> None:
        """订阅共享记忆更新事件."""
        await self._redis.subscribe("shared_memory", callback)
