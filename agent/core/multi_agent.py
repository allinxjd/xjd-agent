"""多 Agent 协作 — Sub-Agent 委派 (核心理念: 主 Agent 可以将子任务委派给专门的 Sub-Agent 处理。
每个 Sub-Agent 有独立的角色定义、工具集和 system prompt。

用法:
    manager = MultiAgentManager(router, tool_registry)
    manager.register_role(AgentRole(name="coder", ...))

    result = await manager.delegate(task="写一个排序算法", role="coder")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.providers.base import Usage

logger = logging.getLogger(__name__)

@dataclass
class AgentRole:
    """Agent 角色定义."""

    name: str  # 角色名 (如 coder, researcher)
    description: str  # 角色描述 (给主 Agent 匹配用)
    system_prompt: str  # 角色专属 system prompt
    model_override: Optional[str] = None  # 可选: 使用不同模型
    tools_filter: list[str] = field(default_factory=list)  # 允许的工具类别
    max_tool_rounds: int = 15  # 子 Agent 最大工具调用轮数
    keywords: list[str] = field(default_factory=list)  # 匹配关键词

@dataclass
class SubAgentResult:
    """子 Agent 执行结果."""

    agent_name: str = ""
    content: str = ""
    tool_calls_made: int = 0
    usage: Usage = field(default_factory=Usage)
    duration_ms: float = 0.0
    success: bool = True
    error: str = ""

# ── 内置角色 ─────────────────────────────────────────────────

BUILTIN_ROLES = [
    AgentRole(
        name="coder",
        description="代码编写专家，擅长编程、调试、重构",
        system_prompt="你是一个代码专家。专注于编写高质量、可维护的代码。使用工具来读写文件、执行代码、运行测试。",
        tools_filter=["code", "file", "terminal"],
        keywords=["代码", "编程", "函数", "类", "bug", "调试", "重构", "code", "debug", "refactor"],
    ),
    AgentRole(
        name="researcher",
        description="信息搜索专家，擅长网络搜索和信息整理",
        system_prompt="你是一个研究专家。专注于搜索、整理和分析信息。使用网络搜索和浏览器工具获取最新信息。",
        tools_filter=["web", "browser"],
        keywords=["搜索", "查找", "研究", "调研", "search", "research", "find"],
    ),
    AgentRole(
        name="analyst",
        description="数据分析专家，擅长数据处理和可视化",
        system_prompt="你是一个数据分析专家。专注于数据查询、处理、分析和可视化。",
        tools_filter=["data", "file", "code"],
        keywords=["数据", "分析", "统计", "查询", "SQL", "data", "analyze", "query"],
    ),
    AgentRole(
        name="executor",
        description="系统操作专家，擅长服务器管理和部署",
        system_prompt="你是一个系统运维专家。专注于执行系统命令、管理进程、部署服务。注意安全，危险操作需确认。",
        tools_filter=["system", "terminal", "network"],
        keywords=["部署", "服务器", "进程", "安装", "配置", "deploy", "server", "install"],
    ),
]

# ── MultiAgentManager ────────────────────────────────────────

class MultiAgentManager:
    """多 Agent 协作管理器.

    用法:
        manager = MultiAgentManager(router, tool_registry)
        result = await manager.delegate(task="写一个排序算法")
    """

    def __init__(
        self,
        router: Any,
        tool_registry: Any,
        memory_manager: Optional[Any] = None,
        skill_manager: Optional[Any] = None,
    ) -> None:
        self._router = router
        self._tool_registry = tool_registry
        self._memory_manager = memory_manager
        self._skill_manager = skill_manager
        self._roles: dict[str, AgentRole] = {}

        # 注册内置角色
        for role in BUILTIN_ROLES:
            self._roles[role.name] = role

    def register_role(self, role: AgentRole) -> None:
        """注册自定义角色."""
        self._roles[role.name] = role
        logger.info("Registered agent role: %s", role.name)

    def list_roles(self) -> list[AgentRole]:
        """列出所有角色."""
        return list(self._roles.values())

    def get_role(self, name: str) -> Optional[AgentRole]:
        """获取角色."""
        return self._roles.get(name)

    async def spawn_agent(
        self,
        role_name: str,
        task: str,
        parent_messages: Optional[list] = None,
    ) -> SubAgentResult:
        """创建子 Agent 执行任务.

        Args:
            role_name: 角色名
            task: 任务描述
            parent_messages: 父 Agent 的上下文消息 (可选，用于传递背景)

        Returns:
            SubAgentResult
        """
        role = self._roles.get(role_name)
        if not role:
            return SubAgentResult(
                agent_name=role_name,
                success=False,
                error=f"未知角色: {role_name}。可用: {', '.join(self._roles.keys())}",
            )

        start = time.time()

        try:
            from agent.core.engine import AgentEngine

            # 创建子 Agent 引擎
            engine = AgentEngine(
                router=self._router,
                system_prompt=role.system_prompt,
                max_tool_rounds=role.max_tool_rounds,
                memory_manager=self._memory_manager,
                skill_manager=self._skill_manager,
            )

            # 注册过滤后的工具
            if self._tool_registry:
                for tool in self._tool_registry.list_tools():
                    if not role.tools_filter or tool.category in role.tools_filter:
                        engine.register_tool(
                            name=tool.name,
                            description=tool.description,
                            parameters=tool.parameters,
                            handler=tool.handler,
                            requires_approval=tool.requires_approval,
                        )

            # 注入父上下文
            if parent_messages:
                context = "\n".join(
                    f"{m.role}: {str(m.content)[:200]}"
                    for m in parent_messages[-5:]
                )
                engine.add_context(f"\n## 任务背景\n{context}")

            # 执行
            result = await engine.run_turn(task)

            duration = (time.time() - start) * 1000
            logger.info(
                "Sub-agent [%s] completed: %d tool calls, %.0fms",
                role_name, result.tool_calls_made, duration,
            )

            return SubAgentResult(
                agent_name=role_name,
                content=result.content,
                tool_calls_made=result.tool_calls_made,
                usage=result.total_usage,
                duration_ms=duration,
            )

        except Exception as e:
            logger.error("Sub-agent [%s] failed: %s", role_name, e)
            return SubAgentResult(
                agent_name=role_name,
                success=False,
                error=str(e),
                duration_ms=(time.time() - start) * 1000,
            )

    def _select_role(self, task: str) -> AgentRole:
        """根据任务描述自动选择最合适的角色."""
        lower = task.lower()
        best_role = None
        best_score = 0

        for role in self._roles.values():
            score = 0
            for kw in role.keywords:
                if kw.lower() in lower:
                    score += 1
            # 描述匹配
            for word in role.description.split():
                if len(word) > 2 and word.lower() in lower:
                    score += 0.5

            if score > best_score:
                best_score = score
                best_role = role

        return best_role or list(self._roles.values())[0]

    async def delegate(
        self,
        task: str,
        role: str = "",
        parent_messages: Optional[list] = None,
        **kwargs,
    ) -> str:
        """委派任务 (可作为工具 handler).

        Args:
            task: 子任务描述
            role: 指定角色 (留空自动选择)

        Returns:
            子 Agent 的回复内容
        """
        if not role:
            selected = self._select_role(task)
            role = selected.name
            logger.info("Auto-selected role: %s (for task: %s)", role, task[:50])

        result = await self.spawn_agent(role, task, parent_messages)

        if not result.success:
            return f"[子 Agent {role} 失败] {result.error}"

        return f"[子 Agent: {role}] {result.content}"

    async def parallel_delegate(
        self,
        tasks: list[dict[str, str]],
    ) -> list[SubAgentResult]:
        """并行委派多个子任务.

        Args:
            tasks: [{"task": "...", "role": "..."}, ...]

        Returns:
            结果列表
        """
        coros = []
        for t in tasks:
            role_name = t.get("role", "")
            if not role_name:
                role_name = self._select_role(t["task"]).name
            coros.append(self.spawn_agent(role_name, t["task"]))

        results = await asyncio.gather(*coros, return_exceptions=True)

        final = []
        for r in results:
            if isinstance(r, Exception):
                final.append(SubAgentResult(success=False, error=str(r)))
            else:
                final.append(r)

        return final
