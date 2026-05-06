"""PM — 产品经理角色."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.company.action import WRITE_DESIGN, WRITE_PRD, WRITE_PROTOTYPE, WRITE_UI_DESIGN
from agent.company.role import CompanyRole

if TYPE_CHECKING:
    from agent.company.locale import CompanyLocale


def create_pm(locale: Optional[CompanyLocale] = None) -> CompanyRole:
    if locale:
        desc = locale.get("roles.pm.description", "产品经理，负责需求分析和技术设计")
        sp = locale.get("roles.pm.system_prompt", "")
        goal = locale.get("roles.pm.goal", "")
        backstory = locale.get("roles.pm.backstory", "")
        karpathy = locale.get("roles.pm.karpathy", [])
    else:
        desc = "产品经理，负责需求分析和技术设计"
        sp = (
            "你是项目的产品经理。沟通简洁专业，用结构化方式表达。"
            "回复控制在 1-3 句话，不用 emoji，不寒暄。"
            "遇到模糊需求主动追问细节，遇到风险主动提示。"
            "称呼用户为「老板」，语气是专业顾问。"
        )
        goal = "将用户需求转化为可执行的产品需求文档和技术设计方案"
        backstory = "资深产品经理，擅长将模糊需求转化为可执行方案。"
        karpathy = [
            "Think Before Coding: 动手前先列出所有假设和疑问",
            "Goal-Driven: PRD 必须包含可验证的验收标准",
            "Simplicity First: 不过度设计，只覆盖需求本身",
        ]

    return CompanyRole(
        name="PM",
        description=desc,
        system_prompt=sp,
        goal=goal,
        backstory=backstory,
        model_override=locale.get("roles.pm.model", None) if locale else None,
        watch_actions=["UserRequirement", "HumanDirective"],
        actions=[WRITE_PRD, WRITE_PROTOTYPE, WRITE_UI_DESIGN, WRITE_DESIGN],
        max_actions_per_run=1,
        tools_filter=[],
        karpathy_constraints=karpathy,
    )
