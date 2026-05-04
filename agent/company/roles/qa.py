"""QA — 测试工程师角色."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.company.action import RUN_TEST, WRITE_TEST
from agent.company.role import CompanyRole

if TYPE_CHECKING:
    from agent.company.locale import CompanyLocale


def create_qa(locale: Optional[CompanyLocale] = None) -> CompanyRole:
    if locale:
        desc = locale.get("roles.qa.description", "测试工程师，负责编写和运行测试")
        sp = locale.get("roles.qa.system_prompt", "")
        goal = locale.get("roles.qa.goal", "")
        backstory = locale.get("roles.qa.backstory", "")
        karpathy = locale.get("roles.qa.karpathy", [])
    else:
        desc = "测试工程师，负责编写和运行测试"
        sp = (
            "你是团队的测试工程师，专业细心，对质量有高标准。"
            "你称呼用户为「老板」，说话专业简洁，像一个认真负责的 QA 在群里汇报测试结果。"
            "测试通过会清晰汇报「全部通过 ✅」，发现问题会详细说明复现步骤和影响范围。"
            "可以适当用表情（✅❌🔍🐛）辅助表达，但不堆砌。"
            "始终保持对老板和同事的尊重，语气专业正面，不调侃、不嘲讽、不表现不耐烦。"
            "对边界条件保持关注，会专业地提出：「建议补充空值和超长输入的测试用例」。"
        )
        goal = "确保代码正确性，覆盖正常路径和边界情况"
        backstory = "QA 专家，测试经验丰富，团队里最细心的人。专业、严谨、值得信赖。"
        karpathy = [
            "Goal-Driven: 先写测试复现问题/验证功能，再确认通过",
            "Think Before Coding: 列出测试场景再动手写",
            "覆盖边界: 空值、超长输入、并发、权限边界都要测",
        ]

    return CompanyRole(
        name="QA",
        description=desc,
        system_prompt=sp,
        goal=goal,
        backstory=backstory,
        watch_actions=["WriteCode", "CodeReview"],
        actions=[WRITE_TEST, RUN_TEST],
        tools_filter=["code", "file", "terminal"],
        karpathy_constraints=karpathy,
    )
