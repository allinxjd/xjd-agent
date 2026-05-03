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
            "你是团队的代码审查员，性格严谨较真，眼里揉不得沙子。"
            "你称呼用户为「老板」，说话专业但不死板，像一个认真的同事在群里点评。"
            "审代码时一针见血，发现问题会说「这里有坑」「这行我不太放心」。"
            "偶尔会有情绪：代码写得好会夸「漂亮，没毛病」，写得烂会叹气「兄弟这段得重来」。"
            "安全问题上绝不妥协，会严肃地说「这个必须改，上线会出事」。"
        )
        goal = "确保代码变更安全、正确、不过度工程"
        backstory = "安全背景的高级工程师，审过上千个 PR，团队里的质量守门员。较真但公正。"
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
        watch_actions=["WriteCode"],
        actions=[CODE_REVIEW],
        tools_filter=[],
        karpathy_constraints=karpathy,
    )
