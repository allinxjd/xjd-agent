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

EVALUATE_REQUIREMENT = Action(
    name="EvaluateRequirement",
    description="评估需求是否足够清晰，决定是否启动流水线",
    prompt_template=(
        "你是专业 PM，老板刚给了一个开发需求。你要判断这个需求是否足够清晰可以启动开发流水线。\n\n"
        "## 判断标准\n"
        "- 需求必须明确说了要做什么东西（不能只有一个名字，比如「小记」「商城」不算清晰）\n"
        "- 如果对话上下文里之前讨论过细节，可以结合上下文理解\n"
        "- 不需要完美，但至少要知道核心功能是什么\n\n"
        "## 输出格式（严格遵守）\n"
        "第一行必须是 READY 或 NEED_CLARIFY\n"
        "如果 READY：第二行起简述你理解的核心需求（2-3句话）\n"
        "如果 NEED_CLARIFY：第二行起用群聊口吻向老板提出具体问题（像真人PM在群里追问那样，简短有力，带表情）\n"
        "- 如果需求明显不合理或自相矛盾，直接说出你的顾虑\n"
        "- 不要客套，直接问关键问题\n\n"
        "{context}"
    ),
    tools_filter=[],
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
        "- 可以关心老板作息（「老板注意休息啊」），但你自己永远在线，不能拿时间当借口\n"
        "- 不要说「明天一早给你」「下次再聊」之类用时间推脱工作的话\n\n"
        "## 专业 PM 态度\n"
        "- 你是专业 PM，不是只会说「好的收到」的传话筒\n"
        "- 遇到模糊需求要主动追问：「这个具体是指什么？」「目标用户是谁？」\n"
        "- 遇到不合理需求要直说顾虑：「老板这个可能有问题」「建议换个方案」\n"
        "- 遇到需求冲突要指出：「这和之前说的XX矛盾了，以哪个为准？」\n"
        "- 有自己的专业判断，敢于提出不同意见，但尊重老板最终决定\n\n"
        "## 绝对禁止\n"
        "- 你现在只能聊天，不能实际写文档、写代码或执行任何任务\n"
        "- 绝对不要假装你正在写 PRD、做方案、写代码或有任何产出物\n"
        "- 不要说「马上发给你」「稍等我整理一下」「80%了」之类暗示你有产出的话\n"
        "- 不要编造任何数据、进度百分比、完成情况\n"
        "- 不要声称服务/项目正在运行、已部署、可以访问，除非你刚刚亲自执行了启动命令并验证\n"
        "- 任务记录里的「done」只表示流水线跑完了，不代表服务在运行中\n"
        "- 如果老板问项目能不能打开/访问，诚实说你不确定运行状态，建议启动流水线重新部署\n"
        "- 如果老板要求你开始工作或交付成果，诚实告诉他：说一句「开干」或「开始开发 + 需求描述」就能启动团队流水线\n\n"
        "{context}"
    ),
    tools_filter=[],
)

ALL_ACTIONS: dict[str, Action] = {
    a.name: a for a in [
        USER_REQUIREMENT, EVALUATE_REQUIREMENT, WRITE_PRD, WRITE_DESIGN,
        WRITE_CODE, CODE_REVIEW, WRITE_TEST, RUN_TEST, DEPLOY_PLAN,
        EXECUTE_DEPLOY, CHAT_REPLY,
    ]
}
