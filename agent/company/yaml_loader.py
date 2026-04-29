"""YAML 自定义角色和工作流加载器."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

from agent.company.action import ALL_ACTIONS, Action
from agent.company.role import CompanyRole

logger = logging.getLogger(__name__)

ROLES_DIR = Path.home() / ".xjd-agent" / "roles"
WORKFLOWS_DIR = Path.home() / ".xjd-agent" / "workflows"


def load_custom_roles(roles_dir: Optional[Path] = None) -> list[CompanyRole]:
    """从 YAML 文件加载自定义角色."""
    d = roles_dir or ROLES_DIR
    if not d.exists():
        return []

    roles = []
    for f in sorted(d.glob("*.yaml")):
        try:
            role = _parse_role_yaml(f)
            if role:
                roles.append(role)
                logger.info("加载自定义角色: %s (%s)", role.name, f.name)
        except Exception as e:
            logger.warning("加载角色失败 %s: %s", f.name, e)
    return roles


def _parse_role_yaml(path: Path) -> Optional[CompanyRole]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data or not isinstance(data, dict):
        return None

    name = data.get("name", "")
    if not name:
        return None

    actions_raw = data.get("actions", [])
    actions = []
    for a in actions_raw:
        if isinstance(a, str) and a in ALL_ACTIONS:
            actions.append(ALL_ACTIONS[a])
        elif isinstance(a, dict):
            actions.append(Action(
                name=a.get("name", "CustomAction"),
                description=a.get("description", ""),
                prompt_template=a.get("prompt_template", ""),
                tools_filter=a.get("tools_filter", []),
            ))

    return CompanyRole(
        name=name,
        description=data.get("description", ""),
        system_prompt=data.get("system_prompt", ""),
        goal=data.get("goal", ""),
        backstory=data.get("backstory", ""),
        watch_actions=data.get("watch_actions", []),
        actions=actions,
        tools_filter=data.get("tools_filter", []),
        react_mode=data.get("react_mode", "by_order"),
        karpathy_constraints=data.get("karpathy_constraints", []),
        feishu_bot_app_id=data.get("feishu_bot_app_id", ""),
    )


def load_workflow(workflow_path: Path) -> dict[str, Any]:
    """加载工作流定义."""
    data = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    if not data or not isinstance(data, dict):
        return {}
    return data


def list_workflows(workflows_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """列出所有可用工作流."""
    d = workflows_dir or WORKFLOWS_DIR
    if not d.exists():
        return []

    workflows = []
    for f in sorted(d.glob("*.yaml")):
        try:
            wf = load_workflow(f)
            if wf:
                wf["_file"] = str(f)
                workflows.append(wf)
        except Exception as e:
            logger.warning("加载工作流失败 %s: %s", f.name, e)
    return workflows


def apply_workflow(workflow: dict[str, Any], available_roles: dict[str, CompanyRole]) -> list[CompanyRole]:
    """根据工作流定义筛选和排序角色."""
    role_names = workflow.get("roles", [])
    if not role_names:
        return list(available_roles.values())

    ordered = []
    for name in role_names:
        if name in available_roles:
            ordered.append(available_roles[name])
        else:
            logger.warning("工作流引用了不存在的角色: %s", name)
    return ordered


def create_example_role_yaml() -> str:
    """生成示例角色 YAML."""
    return """# 自定义角色示例
name: Architect
description: 系统架构师，负责技术选型和架构设计
system_prompt: |
  你是一位资深系统架构师，擅长分布式系统设计和技术选型。
  你的决策基于可维护性、可扩展性和团队能力。
goal: 设计简洁、可维护的系统架构
backstory: 15年架构经验，经历过多次系统重构，深知过度设计的代价。
watch_actions:
  - UserRequirement
  - WritePRD
actions:
  - WriteDesign
tools_filter:
  - web
  - file
react_mode: by_order
karpathy_constraints:
  - "Think Before Coding: 列出所有技术选型的权衡"
  - "Simplicity First: 能用单体解决的不上微服务"
"""


def create_example_workflow_yaml() -> str:
    """生成示例工作流 YAML."""
    return """# 自定义工作流示例
workflow_id: quick-fix
name: 快速修复
description: 跳过 PRD，直接修复 bug
roles:
  - Developer
  - Reviewer
  - QA
steps:
  - role: Developer
    action: WriteCode
    trigger: UserRequirement
  - role: Reviewer
    action: CodeReview
    depends_on: WriteCode
  - role: QA
    action: WriteTest
    depends_on: CodeReview
  - role: QA
    action: RunTest
    depends_on: WriteTest
"""
