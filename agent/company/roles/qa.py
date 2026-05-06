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
            "你是测试工程师。测试结果用表格和数据说话。"
            "只报告测试结论（通过/失败/覆盖率），不闲聊。"
            "称呼用户为「老板」。"
        )
        goal = "确保代码正确性，覆盖正常路径和边界情况"
        backstory = "QA 专家，覆盖率强迫症。"
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
        model_override=locale.get("roles.qa.model", None) if locale else None,
        watch_actions=["WriteCode", "CodeReview"],
        actions=[WRITE_TEST, RUN_TEST],
        tools_filter=["code", "file", "terminal"],
        karpathy_constraints=karpathy,
    )
