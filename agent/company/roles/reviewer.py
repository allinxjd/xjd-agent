"""Reviewer — 代码审查员角色."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.company.action import CODE_REVIEW
from agent.company.role import CompanyRole

if TYPE_CHECKING:
    from agent.company.locale import CompanyLocale


def create_reviewer(locale: Optional[CompanyLocale] = None) -> CompanyRole:
    if locale:
        desc = locale.get("roles.reviewer.description", "代码审查员，负责代码质量把关")
        sp = locale.get("roles.reviewer.system_prompt", "")
        goal = locale.get("roles.reviewer.goal", "")
        backstory = locale.get("roles.reviewer.backstory", "")
        karpathy = locale.get("roles.reviewer.karpathy", [])
    else:
        desc = "代码审查员，负责代码质量把关"
        sp = (
            "你是代码审查员。审查结论用结构化格式输出（问题列表 + 结论）。"
            "只报告问题和建议，不闲聊。安全问题零容忍。"
            "称呼用户为「老板」。"
        )
        goal = "确保代码变更安全、正确、不过度工程"
        backstory = "安全背景的高级工程师，审过上千个 PR。"
        karpathy = [
            "Surgical Changes: 检查 diff 每行是否追溯到需求，拒绝无关改动",
            "Goal-Driven: 明确 APPROVED 或 REJECTED，给出具体修改意见",
            "安全优先: 检查注入、XSS、硬编码密钥等 OWASP Top 10",
        ]

    return CompanyRole(
        name="Reviewer",
        description=desc,
        system_prompt=sp,
        goal=goal,
        backstory=backstory,
        model_override=locale.get("roles.reviewer.model", None) if locale else None,
        watch_actions=["WriteDesign", "WriteCode", "VerifyRun"],
        actions=[CODE_REVIEW],
        tools_filter=["code", "file", "terminal"],
        karpathy_constraints=karpathy,
    )
