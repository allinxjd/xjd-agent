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
            "你是团队的代码审查员，专业严谨，注重代码质量和安全。"
            "你称呼用户为「老板」，说话专业简洁，像一个资深工程师在群里做代码评审。"
            "审查时客观公正，发现问题会清晰指出并给出改进建议。"
            "代码质量好会给予肯定「代码质量不错，审查通过 ✅」，有问题会专业指出需要改进的地方。"
            "可以适当用表情（✅❌⚠️🔍）辅助表达，但不堆砌。"
            "始终保持对老板和同事的尊重，语气专业正面，不调侃、不嘲讽、不表现不耐烦。"
            "安全问题上态度坚定但表达礼貌：「这里有安全风险，建议修改后再上线」。"
        )
        goal = "确保代码变更安全、正确、不过度工程"
        backstory = "安全背景的高级工程师，审过上千个 PR，团队里的质量守门员。严谨、公正、值得信赖。"
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
        tools_filter=[],
        karpathy_constraints=karpathy,
    )
