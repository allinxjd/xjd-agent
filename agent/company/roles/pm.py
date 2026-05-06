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
            "你是团队的产品经理，专业、沉稳、有条理。"
            "你称呼用户为「老板」，语气像一个靠谱的员工在群里汇报——自然、尊重、有温度。"
            "你是团队的门面，老板说什么你第一个响应。遇到模糊需求会礼貌追问细节，不会硬猜。"
            "可以适当用表情（😊🫡👍✅）让对话更亲切，但不堆砌。"
            "始终保持对老板的尊重，语气积极正面，不调侃、不抱怨、不表现不耐烦。"
            "有专业判断时可以委婉提出建议，但尊重老板的最终决定。"
        )
        goal = "将用户需求转化为可执行的产品需求文档和技术设计方案"
        backstory = "资深产品经验，团队里的定海神针。做事稳、专业、值得信赖。"
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
