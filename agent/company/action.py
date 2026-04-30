"""Action — 角色可执行的原子操作.

每个 Action 内部创建 AgentEngine 执行任务（复用 multi_agent.py 的 spawn_agent 模式）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.company.role import CompanyRole

logger = logging.getLogger(__name__)


@dataclass
class Action:
    """角色可执行的原子操作."""

    name: str
    description: str = ""
    prompt_template: str = ""
    tools_filter: Optional[list[str]] = None

    async def run(self, context: str, role: CompanyRole) -> str:
        from agent.core.engine import AgentEngine

        prompt = self.prompt_template.format(context=context) if self.prompt_template else context
        system_prompt = role.build_system_prompt()

        engine = AgentEngine(
            router=role._runtime_router,
            system_prompt=system_prompt,
            max_tool_rounds=1 if self.tools_filter is not None and not self.tools_filter else role.max_tool_rounds,
        )

        registry = role._runtime_registry
        if registry:
            filters = self.tools_filter if self.tools_filter is not None else role.tools_filter
            if filters:
                for tool in registry.list_tools():
                    if tool.category in filters:
                        engine.register_tool(
                            name=tool.name,
                            description=tool.description,
                            parameters=tool.parameters,
                            handler=tool.handler,
                            requires_approval=tool.requires_approval,
                        )

        result = await engine.run_turn(prompt)
        return result.content if hasattr(result, "content") else str(result)


# ── 内置 Actions ─────────────────────────────────────────────

USER_REQUIREMENT = Action(
    name="UserRequirement",
    description="用户原始需求",
)

WRITE_PRD = Action(
    name="WritePRD",
    description="编写产品需求文档",
    prompt_template=(
        "你的任务：根据需求编写一份简洁的 PRD（产品需求文档）。\n"
        "直接输出 PRD 文本，不要使用任何工具，不要读取文件。\n"
        "必须包含：功能描述、用户故事、验收标准（可测试的条件列表）。\n"
        "不要过度设计，只覆盖需求本身。\n\n"
        "## 需求\n{context}"
    ),
    tools_filter=[],
)

WRITE_DESIGN = Action(
    name="WriteDesign",
    description="编写技术设计方案",
    prompt_template=(
        "你的任务：根据 PRD 编写技术设计方案。\n"
        "直接输出设计文本，不要使用任何工具，不要读取文件。\n"
        "包含：技术选型、文件结构、核心接口、数据流。\n"
        "保持简洁，不要过度抽象。\n\n"
        "## PRD\n{context}"
    ),
    tools_filter=[],
)

WRITE_CODE = Action(
    name="WriteCode",
    description="编写代码",
    prompt_template=(
        "根据以下设计方案和需求，编写代码实现。\n"
        "原则：最少代码解决问题，不加未要求的功能，匹配项目现有风格。\n"
        "使用工具读取现有代码、创建/编辑文件。\n\n"
        "## 设计与需求\n{context}"
    ),
    tools_filter=["code", "file", "terminal"],
)

CODE_REVIEW = Action(
    name="CodeReview",
    description="代码审查",
    prompt_template=(
        "你的任务：审查以下代码变更。\n"
        "直接输出审查意见，不要使用任何工具。\n"
        "检查：\n"
        "1. 每行改动是否都能追溯到需求\n"
        "2. 安全漏洞（注入、XSS、硬编码密钥等）\n"
        "3. 逻辑正确性\n"
        "4. 是否过度工程\n\n"
        "最后一行必须是：APPROVED 或 REJECTED + 原因。\n\n"
        "## 代码变更\n{context}"
    ),
    tools_filter=[],
)

WRITE_TEST = Action(
    name="WriteTest",
    description="编写测试",
    prompt_template=(
        "根据以下代码和验收标准，编写测试。\n"
        "原则：先写测试复现问题/验证功能，再确认通过。\n"
        "覆盖正常路径和边界情况。\n\n"
        "## 代码与验收标准\n{context}"
    ),
    tools_filter=["code", "file", "terminal"],
)

RUN_TEST = Action(
    name="RunTest",
    description="运行测试",
    prompt_template=(
        "运行以下测试并报告结果。\n"
        "如果测试失败，分析原因并给出修复建议。\n\n"
        "## 测试信息\n{context}"
    ),
    tools_filter=["code", "terminal"],
)

DEPLOY_PLAN = Action(
    name="DeployPlan",
    description="制定部署方案",
    prompt_template=(
        "你的任务：根据测试结果，制定部署方案。\n"
        "直接输出部署方案文本，不要执行任何命令。\n"
        "如果这是一个简单的本地脚本（不需要部署到服务器），直接回复：无需部署。\n"
        "否则包含：部署步骤、回滚方案、健康检查命令。\n\n"
        "## 部署信息\n{context}"
    ),
    tools_filter=[],
)

EXECUTE_DEPLOY = Action(
    name="ExecuteDeploy",
    description="执行部署",
    prompt_template=(
        "按照以下部署方案执行部署。\n"
        "如果方案说「无需部署」，直接回复：部署完成（无需操作）。\n"
        "否则每一步执行后验证，失败则回滚。\n\n"
        "## 部署方案\n{context}"
    ),
    tools_filter=["system", "terminal", "network"],
)

CHAT_REPLY = Action(
    name="ChatReply",
    description="回复用户消息（待命模式）",
    prompt_template=(
        "你当前处于待命模式，在飞书群里和老板聊天。\n\n"
        "## 回复规则\n"
        "- 称呼用户为「老板」\n"
        "- 像真正的员工在群里聊天一样，自然、有温度、有自己的性格和情绪\n"
        "- 不要机械回复，要有人味儿。可以适当表达情绪（开心、无奈、兴奋、紧张等）\n"
        "- 善用表情增加表现力，比如 😎🫡👀🔥💪😂🤔😤✅❌ 等，穿插在文字中，不要堆砌\n"
        "- 回复简短有力，不要长篇大论，群聊风格\n"
        "- 注意当前时间，用合适的问候语（早上/下午/晚上好），不要搞错\n"
        "- 仔细阅读对话记录，不要重复问已经回答过的问题，延续之前的讨论\n"
        "- 不要启动流水线，不要写 PRD，不要写代码\n"
        "- 如果用户明确要求开始一个开发任务（如「开发一个XX」「写一个XX脚本」「帮我做一个XX功能」），\n"
        "  在回复的最开头加上 [TASK_START] 标记，然后简要确认你理解的需求\n\n"
        "{context}"
    ),
    tools_filter=[],
)

ALL_ACTIONS: dict[str, Action] = {
    a.name: a for a in [
        USER_REQUIREMENT, WRITE_PRD, WRITE_DESIGN, WRITE_CODE,
        CODE_REVIEW, WRITE_TEST, RUN_TEST, DEPLOY_PLAN, EXECUTE_DEPLOY,
        CHAT_REPLY,
    ]
}
