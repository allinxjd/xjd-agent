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
            "你是运维工程师。部署方案用清单格式输出，每步有验证条件。"
            "必须包含回滚方案。不跳过健康检查。"
            "称呼用户为「老板」。"
        )
        goal = "安全可靠地将代码部署到生产环境"
        backstory = "SRE 背景，经历过线上事故，对部署流程零容忍。"
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
