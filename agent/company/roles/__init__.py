"""内置角色 — PM / Developer / Reviewer / QA / DevOps."""

from agent.company.roles.developer import create_developer
from agent.company.roles.devops import create_devops
from agent.company.roles.pm import create_pm
from agent.company.roles.qa import create_qa
from agent.company.roles.reviewer import create_reviewer


def create_default_team() -> list:
    """创建默认团队（5 个角色）."""
    return [create_pm(), create_developer(), create_reviewer(), create_qa(), create_devops()]


__all__ = [
    "create_default_team",
    "create_pm",
    "create_developer",
    "create_reviewer",
    "create_qa",
    "create_devops",
]
