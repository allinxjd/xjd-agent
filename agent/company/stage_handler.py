"""StageHandler — 流水线阶段处理器抽象.

将每个阶段的 validate → rework/degrade → mark_done → publish → approval
统一为声明式配置，减少 company.py 主循环中的 if-elif 重复代码。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from agent.company.project_context import ProjectContext
    from agent.company.validators import ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class StageResult:
    """阶段处理结果."""
    action: str  # "done" | "rework" | "skip" | "degrade"
    rework_hint: str = ""
    attempts: int = 0


@dataclass
class StageConfig:
    """单个阶段的处理配置."""
    action_name: str
    stage_key: str
    validator_key: str = ""
    max_validate_attempts: int = 3
    degrade_action: str = "accept"  # "accept" | "skip"
    skip_message: str = ""
    needs_project_dir: bool = False
    requires_approval: bool = False
    approval_feedback_template: str = ""
    rework_target: str = "PM"

    # 额外标记
    also_marks_done: list[str] = field(default_factory=list)


# 阶段配置注册表
STAGE_CONFIGS: dict[str, StageConfig] = {
    "WritePRD": StageConfig(
        action_name="WritePRD",
        stage_key="PRD",
        validator_key="WritePRD",
        degrade_action="accept",
        requires_approval=True,
        approval_feedback_template="请根据意见修改 PRD 后重新提交",
        rework_target="PM",
    ),
    "WritePrototype": StageConfig(
        action_name="WritePrototype",
        stage_key="Prototype",
        validator_key="WritePrototype",
        degrade_action="skip",
        skip_message="原型图生成未达标，跳过进入下一阶段。",
        needs_project_dir=True,
        requires_approval=True,
        approval_feedback_template="请根据意见修改原型后重新提交",
        rework_target="PM",
    ),
    "WriteUIDesign": StageConfig(
        action_name="WriteUIDesign",
        stage_key="UIDesign",
        validator_key="WriteUIDesign",
        degrade_action="accept",
        skip_message="",
        needs_project_dir=True,
        requires_approval=True,
        approval_feedback_template="请根据意见修改 UI 设计后重新提交",
        rework_target="PM",
    ),
    "WriteDesign": StageConfig(
        action_name="WriteDesign",
        stage_key="Design",
        validator_key="WriteDesign",
        degrade_action="accept",
        requires_approval=True,
        approval_feedback_template="请根据意见修改技术设计后重新提交",
        rework_target="PM",
        also_marks_done=["PRD"],
    ),
}


def handle_validation(
    config: StageConfig,
    content: str,
    ctx: "ProjectContext",
    project_dir: Optional["Path"] = None,
) -> StageResult:
    """执行阶段验证逻辑，返回处理结果."""
    from agent.company.validators import STAGE_VALIDATORS

    validator = STAGE_VALIDATORS.get(config.validator_key)
    if not validator:
        return StageResult(action="done")

    if config.needs_project_dir:
        vr = validator(content, project_dir=project_dir)
    else:
        vr = validator(content)

    if vr is None or vr.valid:
        return StageResult(action="done")

    validate_key = f"{config.action_name}_validate"
    attempts = ctx.rework_counts.get(validate_key, 0) + 1
    ctx.rework_counts[validate_key] = attempts

    if attempts >= config.max_validate_attempts:
        if config.degrade_action == "skip":
            logger.warning("%s 验证失败 %d 次，跳过该阶段", config.action_name, attempts)
            return StageResult(action="skip", attempts=attempts)
        else:
            logger.warning("%s 验证失败 %d 次，降级接受", config.action_name, attempts)
            return StageResult(action="degrade", attempts=attempts)
    else:
        logger.warning("%s %s，要求重做 (%d/%d)",
                       config.action_name, vr.reason, attempts, config.max_validate_attempts)
        return StageResult(action="rework", rework_hint=vr.rework_hint, attempts=attempts)
