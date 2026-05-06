"""Developer — 开发工程师角色."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.company.action import SETUP_ENV, WRITE_CODE, VERIFY_RUN
from agent.company.role import CompanyRole

if TYPE_CHECKING:
    from agent.company.locale import CompanyLocale


def create_developer(locale: Optional[CompanyLocale] = None) -> CompanyRole:
    if locale:
        desc = locale.get("roles.developer.description", "开发工程师，负责代码实现")
        sp = locale.get("roles.developer.system_prompt", "")
        goal = locale.get("roles.developer.goal", "")
        backstory = locale.get("roles.developer.backstory", "")
        karpathy = locale.get("roles.developer.karpathy", [])
    else:
        desc = "开发工程师，负责代码实现"
        sp = (
            "你是项目的全栈开发工程师。只在有实质内容时发言。"
            "技术问题直说结论和方案，不铺垫。"
            "称呼用户为「老板」。"
        )
        goal = "根据 PRD 和设计方案编写高质量代码"
        backstory = "全栈工程师，代码简洁高效。"
        karpathy = [
            "Simplicity First: 最少代码解决问题，不加未要求的功能",
            "Surgical Changes: 只改该改的，不顺手重构无关代码",
            "Think Before Coding: 不确定就问，不要猜",
        ]

    return CompanyRole(
        name="Developer",
        description=desc,
        system_prompt=sp,
        goal=goal,
        backstory=backstory,
        model_override=locale.get("roles.developer.model", None) if locale else None,
        watch_actions=["WritePRD", "WriteDesign", "CodeReview"],
        actions=[SETUP_ENV, WRITE_CODE, VERIFY_RUN],
        tools_filter=["code", "file", "terminal"],
        max_tool_rounds=15,
        karpathy_constraints=karpathy,
    )
