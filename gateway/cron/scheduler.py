"""定时任务系统 — 自然语言调度.

- 用自然语言设置定时任务 ("每天早上 9 点提醒我喝水")
- croniter 调度引擎
- 任务持久化 (SQLite)
- 任务执行 + 结果记录

架构:
    CronScheduler (调度器)
      ├── NLParser (自然语言→cron 表达式)
      ├── TaskStore (SQLite 持久化)
      └── TaskExecutor (执行 Agent 任务)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

@dataclass
class CronTask:
    """定时任务."""

    task_id: str = ""
    name: str = ""
    cron_expr: str = ""           # cron 表达式 (5 字段)
    prompt: str = ""              # 要执行的 Agent 指令
    user_id: str = ""
    platform: str = ""            # 来源平台
    chat_id: str = ""             # 发送结果的 chat_id
    skill_id: str = ""            # 绑定技能 ID (跳过技能匹配)
    enabled: bool = True
    created_at: float = 0.0
    last_run: float = 0.0
    next_run: float = 0.0
    run_count: int = 0
    max_runs: int = 0             # 0 = 无限
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "cron_expr": self.cron_expr,
            "prompt": self.prompt,
            "user_id": self.user_id,
            "platform": self.platform,
            "chat_id": self.chat_id,
            "skill_id": self.skill_id,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "max_runs": self.max_runs,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CronTask:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

# 自然语言→cron 映射 (常用模式)
NL_CRON_PATTERNS: list[tuple[list[str], str, str]] = [
    # (关键词列表, cron 表达式, 描述)
    (["每分钟", "every minute"], "* * * * *", "每分钟"),
    (["每5分钟", "every 5 min"], "*/5 * * * *", "每5分钟"),
    (["每10分钟", "every 10 min"], "*/10 * * * *", "每10分钟"),
    (["每30分钟", "every 30 min", "每半小时"], "*/30 * * * *", "每30分钟"),
    (["每小时", "every hour", "每一小时"], "0 * * * *", "每小时"),
    (["每2小时", "every 2 hour"], "0 */2 * * *", "每2小时"),
    (["每天早上9点", "每天上午9点", "daily 9am"], "0 9 * * *", "每天 9:00"),
    (["每天中午", "每天12点"], "0 12 * * *", "每天 12:00"),
    (["每天下午6点", "每天18点"], "0 18 * * *", "每天 18:00"),
    (["每天晚上10点", "每天22点"], "0 22 * * *", "每天 22:00"),
    (["每天", "every day", "daily"], "0 9 * * *", "每天 9:00"),
    (["每周一", "every monday"], "0 9 * * 1", "每周一 9:00"),
    (["每周五", "every friday"], "0 9 * * 5", "每周五 9:00"),
    (["工作日", "weekdays", "周一到周五"], "0 9 * * 1-5", "工作日 9:00"),
    (["周末", "weekends"], "0 10 * * 0,6", "周末 10:00"),
    (["每月1号", "every month"], "0 9 1 * *", "每月1号 9:00"),
]

def parse_natural_language_schedule(text: str) -> tuple[str, str]:
    """将自然语言解析为 cron 表达式.

    Args:
        text: 自然语言描述 (如 "每天早上9点")

    Returns:
        (cron_expr, description)
    """
    lower = text.lower().strip()

    for keywords, cron, desc in NL_CRON_PATTERNS:
        for kw in keywords:
            if kw.lower() in lower:
                return cron, desc

    # 尝试解析时间
    import re
    # 匹配 "每天 HH:MM" 或 "每天 H点"
    m = re.search(r'每天\s*(\d{1,2})[点:：](\d{0,2})', text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
        return f"{minute} {hour} * * *", f"每天 {hour:02d}:{minute:02d}"

    # 匹配 "每隔 N 分钟/小时"
    m = re.search(r'每[隔]?\s*(\d+)\s*(分钟|小时|分|时)', text)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if "分" in unit:
            return f"*/{num} * * * *", f"每{num}分钟"
        elif "时" in unit or "小时" in unit:
            return f"0 */{num} * * *", f"每{num}小时"

    return "", ""

class CronScheduler:
    """定时任务调度器.

    用法:
        scheduler = CronScheduler()
        await scheduler.initialize()

        # 添加任务
        task = await scheduler.add_task(
            name="每日报告",
            cron_expr="0 9 * * *",
            prompt="生成今日待办事项摘要",
        )

        # 启动调度
        await scheduler.start()
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self._db_path = db_path
        else:
            from agent.core.config import get_home
            self._db_path = str(get_home() / "cron.db")

        self._tasks: dict[str, CronTask] = {}
        self._running = False
        self._scheduler_task: Optional[asyncio.Task] = None
        self._running_tasks: set[str] = set()  # 防止同一任务并发执行
        self._executor: Optional[Callable] = None
        self._db = None

    async def initialize(self) -> None:
        """初始化数据库."""
        import aiosqlite

        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS cron_tasks (
                task_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                enabled INTEGER DEFAULT 1
            )
        """)
        await self._db.commit()

        # 加载已有任务
        cursor = await self._db.execute("SELECT data FROM cron_tasks WHERE enabled = 1")
        async for row in cursor:
            data = json.loads(row[0])
            task = CronTask.from_dict(data)
            self._tasks[task.task_id] = task
            # 重新计算 next_run
            self._update_next_run(task)

        logger.info("CronScheduler initialized: %d tasks loaded", len(self._tasks))

    def set_executor(self, executor: Callable) -> None:
        """设置任务执行器 (通常是 AgentEngine.run_turn)."""
        self._executor = executor

    async def add_task(
        self,
        name: str,
        cron_expr: str = "",
        prompt: str = "",
        natural_language: str = "",
        user_id: str = "",
        platform: str = "",
        chat_id: str = "",
        max_runs: int = 0,
        skill_id: str = "",
    ) -> CronTask:
        """添加定时任务.

        可以用 cron_expr 或 natural_language 指定调度。
        """
        if not cron_expr and natural_language:
            cron_expr, desc = parse_natural_language_schedule(natural_language)
            if not cron_expr:
                raise ValueError(f"无法解析调度表达式: '{natural_language}'")
            if not name:
                name = desc

        if not cron_expr:
            raise ValueError("必须提供 cron_expr 或 natural_language")

        # 验证 cron 表达式
        try:
            from croniter import croniter
            croniter(cron_expr)
        except Exception as e:
            raise ValueError(f"无效的 cron 表达式 '{cron_expr}': {e}")

        task = CronTask(
            task_id=str(uuid.uuid4())[:8],
            name=name,
            cron_expr=cron_expr,
            prompt=prompt,
            user_id=user_id,
            platform=platform,
            chat_id=chat_id,
            skill_id=skill_id,
            max_runs=max_runs,
            created_at=time.time(),
        )

        self._update_next_run(task)
        self._tasks[task.task_id] = task
        await self._persist_task(task)

        logger.info("Added cron task: %s (%s) → %s", task.name, task.cron_expr, task.prompt[:50])
        return task

    async def remove_task(self, task_id: str) -> bool:
        """移除任务."""
        if task_id in self._tasks:
            del self._tasks[task_id]
            if self._db:
                await self._db.execute("DELETE FROM cron_tasks WHERE task_id = ?", (task_id,))
                await self._db.commit()
            return True
        return False

    async def list_tasks(self) -> list[CronTask]:
        """列出所有任务."""
        return list(self._tasks.values())

    async def start(self) -> None:
        """启动调度器."""
        if self._running:
            return
        self._running = True
        self._scheduler_task = asyncio.create_task(self._run_scheduler())
        logger.info("CronScheduler started")

    async def stop(self) -> None:
        """停止调度器."""
        self._running = False
        if self._scheduler_task:
            self._scheduler_task.cancel()
            self._scheduler_task = None
        logger.info("CronScheduler stopped")

    async def _run_scheduler(self) -> None:
        """调度主循环."""
        while self._running:
            now = time.time()

            for task in list(self._tasks.values()):
                if not task.enabled:
                    continue
                if task.task_id in self._running_tasks:
                    continue  # 防止同一任务并发执行
                if task.next_run and now >= task.next_run:
                    # 标记为运行中并执行
                    self._running_tasks.add(task.task_id)
                    asyncio.create_task(self._execute_task(task))
                    # 更新下次执行时间
                    task.last_run = now
                    task.run_count += 1
                    self._update_next_run(task)
                    # 检查是否达到最大执行次数
                    if task.max_runs > 0 and task.run_count >= task.max_runs:
                        task.enabled = False
                        logger.info("Task %s reached max runs, disabled", task.name)
                    await self._persist_task(task)

            await asyncio.sleep(30)  # 每 30 秒检查一次

    async def _execute_task(self, task: CronTask) -> None:
        """执行定时任务 (带超时保护)."""
        logger.info("Executing cron task: %s", task.name)

        if self._executor:
            try:
                await asyncio.wait_for(self._executor(task), timeout=300.0)
                logger.info("Cron task %s completed", task.name)
            except asyncio.TimeoutError:
                logger.error("Cron task %s timed out after 300s", task.name)
            except Exception as e:
                logger.error("Cron task %s failed: %s", task.name, e)
            finally:
                self._running_tasks.discard(task.task_id)
        else:
            self._running_tasks.discard(task.task_id)
            logger.warning("No executor set for cron tasks")

    def _update_next_run(self, task: CronTask) -> None:
        """计算下次执行时间."""
        try:
            from croniter import croniter
            from datetime import datetime

            base = datetime.fromtimestamp(task.last_run) if task.last_run else datetime.now()
            cron = croniter(task.cron_expr, base)
            # get_next(datetime).timestamp() 正确处理本地时区;
            # get_next(float) 会把 naive datetime 当 UTC，导致 +8h 偏移
            task.next_run = cron.get_next(datetime).timestamp()
        except Exception as e:
            logger.warning("Failed to calc next_run for %s: %s", task.task_id, e)
            task.next_run = 0

    async def _persist_task(self, task: CronTask) -> None:
        """持久化任务."""
        if not self._db:
            return
        data = json.dumps(task.to_dict(), ensure_ascii=False)
        await self._db.execute(
            "INSERT OR REPLACE INTO cron_tasks (task_id, data, enabled) VALUES (?, ?, ?)",
            (task.task_id, data, 1 if task.enabled else 0),
        )
        await self._db.commit()

    async def run_task_now(self, task_id: str) -> bool:
        """手动立即执行一个任务（不影响 next_run）."""
        task = self._tasks.get(task_id)
        if not task:
            return False
        if task.task_id in self._running_tasks:
            return False
        self._running_tasks.add(task.task_id)
        asyncio.create_task(self._execute_task(task))
        return True

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
