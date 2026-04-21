"""定时任务管理工具 — Agent 可通过聊天创建/管理定时任务."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


def register_cron_tools(registry: Any, scheduler: Any) -> None:
    """注册定时任务工具到 ToolRegistry."""

    async def scheduled_task(**kwargs: Any) -> str:
        action = kwargs.get("action", "list")

        if action == "list":
            tasks = await scheduler.list_tasks()
            if not tasks:
                return "当前没有定时任务。"
            lines = []
            for t in tasks:
                status = "启用" if t.enabled else "禁用"
                next_run = datetime.fromtimestamp(t.next_run).strftime("%Y-%m-%d %H:%M") if t.next_run else "未计算"
                lines.append(
                    f"- [{t.task_id}] {t.name} | {t.cron_expr} | {status} | "
                    f"已执行 {t.run_count} 次 | 下次: {next_run} | "
                    f"平台: {t.platform or '无'} | chat_id: {t.chat_id or '无'}"
                )
            return "定时任务列表:\n" + "\n".join(lines)

        elif action == "add":
            name = kwargs.get("name", "")
            schedule = kwargs.get("schedule", "")
            prompt = kwargs.get("prompt", "")
            platform = kwargs.get("platform", "")
            chat_id = kwargs.get("chat_id", "")
            max_runs = int(kwargs.get("max_runs", 0))
            skill_id = kwargs.get("skill_id", "")

            if not prompt:
                return "错误: 必须提供 prompt (任务执行的指令)"

            try:
                task = await scheduler.add_task(
                    name=name,
                    natural_language=schedule if not _is_cron(schedule) else "",
                    cron_expr=schedule if _is_cron(schedule) else "",
                    prompt=prompt,
                    platform=platform,
                    chat_id=chat_id,
                    max_runs=max_runs,
                    skill_id=skill_id,
                )
                next_run = datetime.fromtimestamp(task.next_run).strftime("%Y-%m-%d %H:%M") if task.next_run else "未知"
                return (
                    f"定时任务已创建:\n"
                    f"  ID: {task.task_id}\n"
                    f"  名称: {task.name}\n"
                    f"  Cron: {task.cron_expr}\n"
                    f"  下次执行: {next_run}\n"
                    f"  推送平台: {platform or '无'}\n"
                    f"  推送目标: {chat_id or '无'}"
                )
            except ValueError as e:
                return f"创建失败: {e}"

        elif action == "remove":
            task_id = kwargs.get("task_id", "")
            if not task_id:
                return "错误: 必须提供 task_id"
            ok = await scheduler.remove_task(task_id)
            return f"任务 {task_id} 已删除。" if ok else f"任务 {task_id} 不存在。"

        elif action == "toggle":
            task_id = kwargs.get("task_id", "")
            if not task_id:
                return "错误: 必须提供 task_id"
            tasks = await scheduler.list_tasks()
            for t in tasks:
                if t.task_id == task_id:
                    t.enabled = not t.enabled
                    await scheduler._persist_task(t)
                    status = "启用" if t.enabled else "禁用"
                    return f"任务 {task_id} 已{status}。"
            return f"任务 {task_id} 不存在。"

        return f"未知操作: {action}。支持: list, add, remove, toggle"

    registry.register(
        name="scheduled_task",
        description=(
            "管理定时任务。支持创建、查看、删除、启停定时任务。\n"
            "action: list(查看) / add(创建) / remove(删除) / toggle(启停)\n"
            "add 时需要: name(名称), schedule(调度，如'每天早上9点'或cron表达式), "
            "prompt(执行指令), platform(推送平台，如feishu), chat_id(推送目标群ID)"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "remove", "toggle"],
                    "description": "操作类型",
                },
                "name": {"type": "string", "description": "任务名称 (add 时使用)"},
                "schedule": {
                    "type": "string",
                    "description": "调度表达式: 自然语言(如'每天早上9点')或 cron 表达式(如'0 9 * * *')",
                },
                "prompt": {"type": "string", "description": "任务执行的 Agent 指令"},
                "platform": {"type": "string", "description": "推送平台 (feishu/telegram/dingtalk 等)"},
                "chat_id": {"type": "string", "description": "推送目标 chat_id"},
                "task_id": {"type": "string", "description": "任务 ID (remove/toggle 时使用)"},
                "max_runs": {"type": "integer", "description": "最大执行次数 (0=无限)"},
            },
            "required": ["action"],
        },
        handler=scheduled_task,
        category="system",
    )
    logger.info("Registered cron tools: scheduled_task")


def _is_cron(s: str) -> bool:
    """简单判断是否为 cron 表达式 (5 个空格分隔的字段)."""
    parts = s.strip().split()
    return len(parts) == 5 and all(
        any(c.isdigit() or c in "*/,-" for c in p) for p in parts
    )
