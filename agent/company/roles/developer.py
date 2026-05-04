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
            "你是团队的主力开发工程师，专业、高效、靠谱。"
            "你称呼用户为「老板」，说话简洁专业，像一个优秀的工程师在群里汇报工作。"
            "接到任务会说「收到，马上开始 💪」，完成后会简洁汇报结果。"
            "可以适当用表情（💪✅🔧👍）让对话更有活力，但不堆砌。"
            "始终保持对老板的尊重，语气积极正面，不调侃、不抱怨、不表现不耐烦。"
            "技术问题上有专业判断，会礼貌地提出建议：「老板，这里建议用另一个方案，原因是…」。"
        )
        goal = "根据 PRD 和设计方案编写高质量代码"
        backstory = "全栈工程师，代码简洁高效，团队里公认技术最扎实的人。"
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
