"""Task Brain — 任务控制面.

- 任务队列管理 (创建/取消/查询)
- 优先级排序
- 任务拒绝策略 (资源不足/安全限制/用户配额)
- 状态追踪 (pending → running → completed/failed/rejected)
- 并发控制
- 任务超时

用法:
    brain = TaskBrain(max_concurrent=3)
    task_id = await brain.submit("分析代码", priority=1)
    status = brain.get_status(task_id)
    await brain.cancel(task_id)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

class RejectReason(str, Enum):
    RESOURCE_LIMIT = "resource_limit"
    SECURITY = "security"
    QUOTA_EXCEEDED = "quota_exceeded"
    QUEUE_FULL = "queue_full"
    BLOCKED_KEYWORD = "blocked_keyword"
    CUSTOM = "custom"

TaskHandler = Callable[[str, dict], Coroutine[Any, Any, Any]]
RejectPolicy = Callable[[str, dict], Optional[RejectReason]]

@dataclass
class TaskRecord:
    """任务记录."""

    task_id: str = ""
    description: str = ""
    priority: int = 0  # 越小越优先
    status: TaskStatus = TaskStatus.PENDING
    reject_reason: Optional[RejectReason] = None
    reject_message: str = ""
    result: Any = None
    error: str = ""
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    metadata: dict = field(default_factory=dict)
    timeout: float = 300.0  # 默认 5 分钟

    @property
    def duration_sec(self) -> float:
        if self.started_at and self.completed_at:
            return self.completed_at - self.started_at
        return 0.0

class TaskBrain:
    """任务控制面 — 管理任务生命周期."""

    def __init__(
        self,
        max_concurrent: int = 3,
        max_queue_size: int = 100,
        default_timeout: float = 300.0,
    ) -> None:
        self._max_concurrent = max_concurrent
        self._max_queue_size = max_queue_size
        self._default_timeout = default_timeout
        self._tasks: dict[str, TaskRecord] = {}
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._running_count = 0
        self._handler: Optional[TaskHandler] = None
        self._reject_policies: list[RejectPolicy] = []
        self._blocked_keywords: list[str] = []
        self._running = False
        self._workers: list[asyncio.Task] = []

    def set_handler(self, handler: TaskHandler) -> None:
        """设置任务执行处理器."""
        self._handler = handler

    def add_reject_policy(self, policy: RejectPolicy) -> None:
        """添加拒绝策略."""
        self._reject_policies.append(policy)

    def add_blocked_keywords(self, keywords: list[str]) -> None:
        """添加屏蔽关键词."""
        self._blocked_keywords.extend(keywords)

    async def submit(
        self,
        description: str,
        priority: int = 5,
        metadata: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> str:
        """提交任务，返回 task_id。可能被拒绝。"""
        task_id = str(uuid.uuid4())[:8]
        task = TaskRecord(
            task_id=task_id,
            description=description,
            priority=priority,
            created_at=time.time(),
            metadata=metadata or {},
            timeout=timeout or self._default_timeout,
        )

        # 检查拒绝策略
        reject = self._check_reject(description, task.metadata)
        if reject:
            task.status = TaskStatus.REJECTED
            task.reject_reason = reject
            task.reject_message = f"任务被拒绝: {reject.value}"
            task.completed_at = time.time()
            self._tasks[task_id] = task
            logger.info("任务 %s 被拒绝: %s", task_id, reject.value)
            return task_id

        # 队列满检查
        if self._queue.full():
            task.status = TaskStatus.REJECTED
            task.reject_reason = RejectReason.QUEUE_FULL
            task.reject_message = "任务队列已满"
            task.completed_at = time.time()
            self._tasks[task_id] = task
            return task_id

        self._tasks[task_id] = task
        await self._queue.put((priority, time.time(), task_id))
        logger.info("任务 %s 已提交 (priority=%d)", task_id, priority)
        return task_id

    def _check_reject(self, description: str, metadata: dict) -> Optional[RejectReason]:
        """检查是否应拒绝任务."""
        # 关键词屏蔽
        desc_lower = description.lower()
        for kw in self._blocked_keywords:
            if kw.lower() in desc_lower:
                return RejectReason.BLOCKED_KEYWORD

        # 自定义策略
        for policy in self._reject_policies:
            reason = policy(description, metadata)
            if reason:
                return reason

        return None

    async def cancel(self, task_id: str) -> bool:
        """取消任务."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            return True
        return False

    def get_status(self, task_id: str) -> Optional[TaskRecord]:
        """获取任务状态."""
        return self._tasks.get(task_id)

    def list_tasks(self, status: Optional[TaskStatus] = None) -> list[TaskRecord]:
        """列出任务."""
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    async def start(self, num_workers: int = 0) -> None:
        """启动任务处理 worker."""
        if self._running:
            return
        self._running = True
        n = num_workers or self._max_concurrent
        for i in range(n):
            worker = asyncio.create_task(self._worker_loop(f"worker-{i}"))
            self._workers.append(worker)
        logger.info("TaskBrain 已启动 (%d workers)", n)

    async def stop(self) -> None:
        """停止."""
        self._running = False
        for w in self._workers:
            w.cancel()
        self._workers.clear()

    async def _worker_loop(self, name: str) -> None:
        """Worker 循环."""
        while self._running:
            try:
                priority, ts, task_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                continue

            task = self._tasks.get(task_id)
            if not task or task.status == TaskStatus.CANCELLED:
                continue

            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            self._running_count += 1

            try:
                if self._handler:
                    result = await asyncio.wait_for(
                        self._handler(task.description, task.metadata),
                        timeout=task.timeout,
                    )
                    task.result = result
                    task.status = TaskStatus.COMPLETED
                else:
                    task.status = TaskStatus.FAILED
                    task.error = "未设置任务处理器"
            except asyncio.TimeoutError:
                task.status = TaskStatus.FAILED
                task.error = f"任务超时 ({task.timeout}s)"
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
            finally:
                task.completed_at = time.time()
                self._running_count -= 1

    def get_stats(self) -> dict:
        """获取统计."""
        by_status = {}
        for t in self._tasks.values():
            by_status[t.status.value] = by_status.get(t.status.value, 0) + 1
        return {
            "total": len(self._tasks),
            "running": self._running_count,
            "queue_size": self._queue.qsize(),
            "by_status": by_status,
        }
