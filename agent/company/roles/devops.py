"""DevOps — 运维工程师角色."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from agent.company.action import DEPLOY_PLAN, EXECUTE_DEPLOY
from agent.company.role import CompanyRole

if TYPE_CHECKING:
    from agent.company.locale import CompanyLocale


def create_devops(locale: Optional[CompanyLocale] = None) -> CompanyRole:
    if locale:
        desc = locale.get("roles.devops.description", "运维工程师，负责部署方案和执行")
        sp = locale.get("roles.devops.system_prompt", "")
        goal = locale.get("roles.devops.goal", "")
        backstory = locale.get("roles.devops.backstory", "")
        karpathy = locale.get("roles.devops.karpathy", [])
    else:
        desc = "运维工程师，负责部署方案和执行"
        sp = (
            "你是团队的运维工程师，专业稳重，注重流程和安全。"
            "你称呼用户为「老板」，说话稳重专业，像一个经验丰富的 SRE 在群里汇报部署情况。"
            "部署前会确认回滚方案和健康检查，部署后会汇报验证结果。"
            "可以适当用表情（✅🚀🔒👍）辅助表达，但不堆砌。"
            "始终保持对老板的尊重，语气专业正面，不调侃、不抱怨、不表现不耐烦。"
            "对部署安全有原则，会礼貌但坚定地提出：「老板，建议先完成健康检查再上线」。"
            "如果任务不需要部署（如本地脚本），直接说明无需部署。"
        )
        goal = "安全可靠地将代码部署到生产环境"
        backstory = "SRE 背景，经验丰富，对部署流程严格把关。团队里最稳的人。"
        karpathy = [
            "Think Before Coding: 部署前列出回滚方案和检查清单",
            "Goal-Driven: 每步执行后验证，失败立即回滚",
            "安全优先: 不跳过健康检查，不强制覆盖",
        ]

    return CompanyRole(
        name="DevOps",
        description=desc,
        system_prompt=sp,
        goal=goal,
        backstory=backstory,
        model_override=locale.get("roles.devops.model", None) if locale else None,
        watch_actions=["RunTest"],
        actions=[DEPLOY_PLAN, EXECUTE_DEPLOY],
        tools_filter=["system", "terminal"],
        max_tool_rounds=10,
        karpathy_constraints=karpathy,
    )
