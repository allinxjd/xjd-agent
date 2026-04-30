"""Company — 团队编排主循环."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from agent.company.action import USER_REQUIREMENT
from agent.company.environment import CompanyEnvironment
from agent.company.feishu_bridge import FeishuBotConfig, FeishuBridge
from agent.company.memory import CompanyMemory
from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole
from agent.company.store import CompanyStore
from agent.company.task import CompanyTask

logger = logging.getLogger(__name__)

KARPATHY_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
KARPATHY_SKILL_NAMES = [
    "karpathy-think-before-coding",
    "karpathy-simplicity-first",
    "karpathy-surgical-changes",
    "karpathy-goal-driven",
]


def _load_karpathy_guidelines() -> str:
    """从 skills/ 目录加载 Karpathy 四原则，拼接为 system prompt 片段."""
    parts = []
    for name in KARPATHY_SKILL_NAMES:
        skill_file = KARPATHY_SKILLS_DIR / name / "SKILL.md"
        if not skill_file.exists():
            continue
        text = skill_file.read_text(encoding="utf-8")
        sep = text.find("---", 3)
        if sep > 0:
            body = text[sep + 3:].strip()
        else:
            body = text
        parts.append(body)
    if not parts:
        return ""
    return "## Karpathy 行为准则\n\n" + "\n\n---\n\n".join(parts)


class Company:
    """多角色编排器."""

    def __init__(
        self,
        router: Any,
        tool_registry: Any = None,
        memory_manager: Any = None,
        feishu_chat_id: str = "",
        feishu_bots: Optional[list[FeishuBotConfig]] = None,
    ) -> None:
        self._env = CompanyEnvironment()
        self._router = router
        self._registry = tool_registry
        self._memory = memory_manager
        self._tasks: dict[str, CompanyTask] = {}
        self._karpathy_prompt = _load_karpathy_guidelines()
        self._feishu_bridge: Optional[FeishuBridge] = None
        self._store = CompanyStore()
        self._store.open()
        self._shared_memory = CompanyMemory(memory_manager)

        if feishu_chat_id and feishu_bots:
            self._feishu_bridge = FeishuBridge(
                group_chat_id=feishu_chat_id,
                bot_configs=feishu_bots,
                environment=self._env,
            )
            self._env._feishu_bridge = self._feishu_bridge

    @property
    def environment(self) -> CompanyEnvironment:
        return self._env

    def hire(self, role: CompanyRole) -> None:
        """注册角色到环境，注入运行时依赖和 Karpathy 准则."""
        role._runtime_router = self._router
        role._runtime_registry = self._registry
        if self._karpathy_prompt and self._karpathy_prompt not in (role.system_prompt or ""):
            role.system_prompt = (role.system_prompt or "") + "\n\n" + self._karpathy_prompt
        self._env.add_role(role)

    def hire_team(self, roles: list[CompanyRole]) -> None:
        for role in roles:
            self.hire(role)

    async def start_feishu(self) -> None:
        """启动飞书桥接（如果已配置）."""
        if self._feishu_bridge:
            await self._feishu_bridge.start()

    async def stop_feishu(self) -> None:
        """停止飞书桥接."""
        if self._feishu_bridge:
            await self._feishu_bridge.stop()

    async def assign(self, task: CompanyTask) -> None:
        """分配任务，发布触发消息."""
        self._tasks[task.task_id] = task
        task.status = "in_progress"
        self._store.save_task(task)

        msg = CompanyMessage(
            content=task.description,
            cause_by=USER_REQUIREMENT.name,
            sent_from="System",
            task_id=task.task_id,
        )
        if task.assigned_to:
            msg.send_to = task.assigned_to

        await self._env.publish(msg)
        self._store.save_message(msg)

    async def run(self, requirement: str, max_rounds: int = 20) -> str:
        """主循环：发布需求 → 角色轮转 → 直到空闲或达到上限."""
        import uuid
        run_id = uuid.uuid4().hex[:8]

        task = CompanyTask(
            title=requirement[:60],
            description=requirement,
        )
        await self.assign(task)
        self._store.save_run(run_id, requirement, task.task_id)

        round_num = 0
        for round_num in range(1, max_rounds + 1):
            if self._env.is_idle():
                logger.info("所有角色空闲，结束 (round %d)", round_num)
                break

            logger.info("=== Round %d ===", round_num)
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                result_msg = await role.run()
                if result_msg:
                    result_msg.task_id = task.task_id
                    task.result = result_msg.content
                    await self._env.publish(result_msg)
                    self._store.save_message(result_msg)
        else:
            logger.warning("达到最大轮次 %d，强制结束", max_rounds)

        if task.result and task.expected_output:
            passed, feedback = await task.verify(self._router)
            if passed:
                task.status = "done"
            else:
                task.status = "failed"
                task.retry_count += 1
                if task.retry_count <= task.max_retries:
                    logger.info("任务验证失败，重试 (%d/%d): %s",
                                task.retry_count, task.max_retries, feedback)
                    retry_msg = CompanyMessage(
                        content=f"任务验证未通过，请根据以下反馈修改：\n{feedback}\n\n原始需求：{requirement}",
                        cause_by="CodeReview",
                        sent_from="System",
                        task_id=task.task_id,
                    )
                    await self._env.publish(retry_msg)
                    self._store.save_message(retry_msg)
                    self._store.save_task(task)
                    return await self._continue_run(task, requirement, max_rounds - round_num)
                else:
                    logger.warning("任务验证失败且已达最大重试: %s", feedback)
        else:
            task.status = "done"

        self._store.save_task(task)
        self._store.finish_run(run_id, task.status, round_num, task.result[:500] if task.result else "")
        return task.result

    async def _continue_run(self, task: CompanyTask, requirement: str, remaining_rounds: int) -> str:
        """验证失败后继续执行剩余轮次."""
        for round_num in range(1, remaining_rounds + 1):
            if self._env.is_idle():
                break
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                result_msg = await role.run()
                if result_msg:
                    task.result = result_msg.content
                    await self._env.publish(result_msg)

        if task.result and task.expected_output:
            passed, feedback = await task.verify(self._router)
            if passed:
                task.status = "done"
            else:
                task.retry_count += 1
                if task.retry_count <= task.max_retries:
                    retry_msg = CompanyMessage(
                        content=f"任务验证未通过，请修改：\n{feedback}",
                        cause_by="CodeReview",
                        sent_from="System",
                        task_id=task.task_id,
                    )
                    await self._env.publish(retry_msg)
                    return await self._continue_run(task, remaining_rounds // 2)
                task.status = "failed"
        else:
            task.status = "done"

        return task.result

    async def run_interactive(self, requirement: str, max_rounds: int = 50) -> str:
        """交互模式：每轮结束后等待用户输入."""
        import asyncio
        import sys

        task = CompanyTask(
            title=requirement[:60],
            description=requirement,
        )
        await self.assign(task)

        for round_num in range(1, max_rounds + 1):
            if self._env.is_idle():
                print(f"\n[Company] 所有角色空闲，结束 (round {round_num})")
                break

            print(f"\n=== Round {round_num} ===")
            for role in self._env.roles.values():
                if not role.has_pending:
                    continue
                result_msg = await role.run()
                if result_msg:
                    task.result = result_msg.content
                    print(f"\n[{result_msg.sent_from}] ({result_msg.cause_by})")
                    print(result_msg.content[:500])
                    if len(result_msg.content) > 500:
                        print("... (truncated)")
                    await self._env.publish(result_msg)

            if self._env.is_idle():
                break

            print("\n[输入指令 / 回车继续 / q 退出]> ", end="", flush=True)
            loop = asyncio.get_event_loop()
            user_input = await loop.run_in_executor(None, sys.stdin.readline)
            user_input = user_input.strip()

            if user_input.lower() in ("q", "quit", "exit"):
                print("[Company] 用户退出")
                break
            elif user_input:
                directive = CompanyMessage(
                    content=user_input,
                    cause_by="HumanDirective",
                    sent_from="Human",
                )
                await self._env.publish(directive)

        task.status = "done" if task.result else "failed"
        return task.result
