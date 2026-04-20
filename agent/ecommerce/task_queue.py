"""分布式任务队列 — 基于 Redis Streams 的电商任务分发.

按角色分 stream，支持任务生命周期管理、优先级、超时和重试。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

TASK_STREAM_PREFIX = "xjd:tasks:"
TASK_META_PREFIX = "xjd:task_meta:"


class TaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ECommerceTask:
    """电商任务."""

    task_id: str = ""
    role: str = ""  # 目标角色
    action: str = ""  # 动作 (如 "query_order")
    payload: dict[str, Any] = field(default_factory=dict)
    priority: int = 0  # 0=normal, 1=high, 2=urgent
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = 0.0
    claimed_by: str = ""
    result: str = ""
    error: str = ""
    max_retries: int = 3
    retry_count: int = 0
    timeout: int = 30  # 秒


class TaskQueue:
    """电商任务队列 — 基于 RedisManager.

    用法:
        queue = TaskQueue(redis_manager)
        task_id = await queue.submit("order", "query_order", {"order_id": "12345"})
        task = await queue.claim("order", "order-agent-1")
        await queue.complete(task_id, result="订单已发货")
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def submit(
        self,
        role: str,
        action: str,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
        timeout: int = 30,
    ) -> str:
        """提交任务到队列."""
        import uuid
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        now = time.time()

        task_data = {
            "task_id": task_id,
            "role": role,
            "action": action,
            "payload": json.dumps(payload or {}),
            "priority": str(priority),
            "status": TaskStatus.PENDING.value,
            "created_at": str(now),
            "max_retries": "3",
            "timeout": str(timeout),
        }

        # 写入 stream
        stream = f"{TASK_STREAM_PREFIX}{role}"
        await self._redis.enqueue(f"tasks:{role}", task_data)

        # 写入元数据
        r = self._redis._redis
        await r.hset(f"{TASK_META_PREFIX}{task_id}", mapping=task_data)
        await r.expire(f"{TASK_META_PREFIX}{task_id}", 3600)

        logger.info("Task submitted: %s → %s.%s", task_id, role, action)
        return task_id

    async def claim(self, role: str, agent_id: str) -> Optional[ECommerceTask]:
        """认领任务."""
        messages = await self._redis.dequeue(
            f"tasks:{role}", group=f"group_{role}", consumer=agent_id,
            block=5000, count=1,
        )
        if not messages:
            return None

        msg = messages[0]
        data = msg.data

        task = ECommerceTask(
            task_id=data.get("task_id", msg.message_id),
            role=role,
            action=data.get("action", ""),
            payload=json.loads(data.get("payload", "{}")) if isinstance(data.get("payload"), str) else data.get("payload", {}),
            priority=int(data.get("priority", 0)),
            status=TaskStatus.CLAIMED,
            created_at=float(data.get("created_at", 0)),
            claimed_by=agent_id,
            timeout=int(data.get("timeout", 30)),
        )

        # 更新元数据
        r = self._redis._redis
        meta_key = f"{TASK_META_PREFIX}{task.task_id}"
        await r.hset(meta_key, mapping={
            "status": TaskStatus.CLAIMED.value,
            "claimed_by": agent_id,
        })

        # ACK
        await self._redis.ack(f"tasks:{role}", f"group_{role}", msg.message_id)

        logger.info("Task claimed: %s by %s", task.task_id, agent_id)
        return task

    async def complete(self, task_id: str, result: str = "") -> None:
        """标记任务完成."""
        r = self._redis._redis
        meta_key = f"{TASK_META_PREFIX}{task_id}"
        await r.hset(meta_key, mapping={
            "status": TaskStatus.COMPLETED.value,
            "result": result,
        })
        logger.info("Task completed: %s", task_id)

        # 发布完成事件
        await self._redis.publish("task_events", {
            "event": "completed", "task_id": task_id, "result": result,
        })

    async def fail(self, task_id: str, error: str = "") -> None:
        """标记任务失败."""
        r = self._redis._redis
        meta_key = f"{TASK_META_PREFIX}{task_id}"
        data = await r.hgetall(meta_key)

        retry_count = int(data.get("retry_count", 0)) + 1
        max_retries = int(data.get("max_retries", 3))

        if retry_count < max_retries:
            # 重新入队
            role = data.get("role", "")
            await r.hset(meta_key, mapping={
                "status": TaskStatus.PENDING.value,
                "retry_count": str(retry_count),
                "error": error,
            })
            await self._redis.enqueue(f"tasks:{role}", dict(data))
            logger.warning("Task retrying (%d/%d): %s", retry_count, max_retries, task_id)
        else:
            await r.hset(meta_key, mapping={
                "status": TaskStatus.FAILED.value,
                "error": error,
            })
            logger.error("Task failed permanently: %s — %s", task_id, error)

        await self._redis.publish("task_events", {
            "event": "failed", "task_id": task_id, "error": error,
        })

    async def get_status(self, task_id: str) -> Optional[ECommerceTask]:
        """查询任务状态."""
        r = self._redis._redis
        data = await r.hgetall(f"{TASK_META_PREFIX}{task_id}")
        if not data:
            return None

        return ECommerceTask(
            task_id=data.get("task_id", task_id),
            role=data.get("role", ""),
            action=data.get("action", ""),
            payload=json.loads(data.get("payload", "{}")) if isinstance(data.get("payload"), str) else data.get("payload", {}),
            status=TaskStatus(data.get("status", "pending")),
            created_at=float(data.get("created_at", 0)),
            claimed_by=data.get("claimed_by", ""),
            result=data.get("result", ""),
            error=data.get("error", ""),
        )
