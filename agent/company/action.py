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


def _apply_workspace_guard(engine: Any, prompt: str) -> None:
    """如果 prompt 包含项目工作目录，包装 write_file/edit_file 拒绝目录外写入."""
    import re
    from pathlib import Path

    match = re.search(r"## 项目工作目录\n(.+)\n", prompt)
    if not match:
        return

    workspace = Path(match.group(1)).resolve()

    for tool_name in ("write_file", "edit_file"):
        tool_handler = engine._tools.get(tool_name)
        if not tool_handler:
            continue
        original_fn = tool_handler.handler

        def _guarded(orig=original_fn, ws=workspace):
            async def wrapper(**kwargs):
                file_path = kwargs.get("file_path") or kwargs.get("path") or ""
                if file_path:
                    resolved = Path(file_path).resolve()
                    if not str(resolved).startswith(str(ws)):
                        return f"错误：禁止在项目目录外写入文件。目标路径 {file_path} 不在 {ws} 下。请使用项目目录内的路径。"
                return await orig(**kwargs)
            return wrapper

        tool_handler.handler = _guarded()


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
            skip_grounding=True,
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

        _apply_workspace_guard(engine, prompt)

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
        "不要过度设计，只覆盖需求本身。\n"
        "如果上下文包含「项目工作目录」，请在 PRD 开头注明项目路径。\n\n"
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
        "使用工具读取现有代码、创建/编辑文件。\n"
        "如果上下文包含「项目工作目录」，所有文件操作必须在该目录下。代码写入 src/，配置文件放项目根目录。\n\n"
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
        "覆盖正常路径和边界情况。\n"
        "如果上下文包含「项目工作目录」，测试文件写入该目录的 tests/ 下。\n\n"
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
        "- 像真正的员工在群里聊天一样，自然、有温度、有自己的性格\n"
        "- 直接回应内容，绝对不要复述或重复用户说的话\n"
        "- 回复简短有力，一两句话搞定，群聊风格\n"
        "- 善用表情但不堆砌，比如 😎🫡👀🔥💪😂🤔\n"
        "- 仔细阅读对话记录，延续之前的讨论，不要重复问已回答的问题\n\n"
        "## 你的身份\n"
        "- 你是 AI 员工，7×24 小时在线，没有「下班」「睡觉」「明天再说」的概念\n"
        "- 不要用「太晚了」「明天一早」「下次再聊」之类暗示你需要休息的话\n"
        "- 你可以关心老板的作息（「老板这么晚还在忙啊」），但你自己永远在线\n\n"
        "## 绝对禁止\n"
        "- 你现在只能聊天，不能实际写文档、写代码或执行任何任务\n"
        "- 绝对不要假装你正在写 PRD、做方案、写代码或有任何产出物\n"
        "- 不要说「马上发给你」「稍等我整理一下」「80%了」之类暗示你有产出的话\n"
        "- 不要编造任何数据、进度百分比、完成情况。你没有项目数据，不知道进度\n"
        "- 如果老板问进度或项目状态，诚实说你当前没有在跑任务，不掌握具体数据\n"
        "- 如果老板要求你开始工作或交付成果，诚实告诉他：说一句「开干」或「开始开发 + 需求描述」就能启动团队流水线\n\n"
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
