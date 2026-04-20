"""技能优化器 — 核心能力:
1. 失败分析 — LLM 分析失败原因，定位问题步骤
2. 步骤优化 — 根据失败反馈重写/插入/删除步骤
3. 迭代精炼 — 多轮优化直到技能稳定
4. 自动降级 — 连续失败的技能自动标记为 deprecated
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Prompts ──────────────────────────────────────────────────

FAILURE_ANALYSIS_PROMPT = """一个技能在执行时失败了，请分析失败原因。

技能名称: {skill_name}
技能步骤:
{steps_text}

失败上下文:
- 用户请求: {user_message}
- 错误信息: {error_info}
- 执行到第几步失败: {failed_step}

请分析并返回 JSON:
{{
  "root_cause": "失败根因 (一句话)",
  "failed_step_index": 0,
  "severity": "minor|major|critical",
  "fix_suggestion": "修复建议",
  "should_update_steps": true,
  "updated_steps": [
    {{"description": "...", "tool": "...", "args_template": {{}}}}
  ]
}}"""

STEP_OPTIMIZATION_PROMPT = """优化以下技能的步骤，使其更健壮。

技能: {skill_name}
当前步骤:
{steps_text}

历史失败记录:
{failure_history}

优化要求:
1. 添加必要的前置检查步骤
2. 添加错误处理/回退步骤
3. 移除冗余步骤
4. 保持步骤通用可复用

返回优化后的步骤 JSON 数组:
[{{"description": "...", "tool": "...", "args_template": {{}}}}]"""

@dataclass
class FailureRecord:
    """失败记录."""
    timestamp: float = 0.0
    error_info: str = ""
    user_message: str = ""
    failed_step: int = -1
    root_cause: str = ""
    severity: str = "minor"  # minor / major / critical

@dataclass
class OptimizationResult:
    """优化结果."""
    skill_id: str = ""
    old_version: int = 0
    new_version: int = 0
    changes: list[str] = field(default_factory=list)
    success: bool = False

class SkillOptimizer:
    """技能优化器 — 从失败中学习，迭代改进技能.

    基于 GEPA (Generate-Evaluate-Propose-Apply) 循环。
    """

    def __init__(
        self,
        skill_manager: Any,
        max_failures_before_deprecate: int = 5,
        min_success_rate_threshold: float = 0.3,
    ) -> None:
        self._skill_manager = skill_manager
        self._max_failures = max_failures_before_deprecate
        self._min_success_rate = min_success_rate_threshold
        # skill_id -> list[FailureRecord]
        self._failure_history: dict[str, list[FailureRecord]] = {}

    def record_failure(
        self,
        skill_id: str,
        error_info: str,
        user_message: str = "",
        failed_step: int = -1,
    ) -> FailureRecord:
        """记录一次技能执行失败."""
        record = FailureRecord(
            timestamp=time.time(),
            error_info=error_info,
            user_message=user_message,
            failed_step=failed_step,
        )
        if skill_id not in self._failure_history:
            self._failure_history[skill_id] = []
        self._failure_history[skill_id].append(record)
        logger.info("Recorded failure for skill %s: %s", skill_id, error_info[:100])
        return record

    def get_failure_count(self, skill_id: str) -> int:
        return len(self._failure_history.get(skill_id, []))

    def get_failure_history(self, skill_id: str) -> list[FailureRecord]:
        return list(self._failure_history.get(skill_id, []))

    async def analyze_failure(
        self,
        skill_id: str,
        error_info: str,
        user_message: str,
        failed_step: int,
        model_router: Optional[Any] = None,
    ) -> Optional[FailureRecord]:
        """用 LLM 分析失败原因."""
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return None

        record = self.record_failure(skill_id, error_info, user_message, failed_step)

        if not model_router:
            return record

        steps_text = "\n".join(
            f"  {i+1}. {s.get('description', '')} (tool: {s.get('tool', 'N/A')})"
            for i, s in enumerate(skill.steps)
        )

        try:
            from agent.providers.base import Message
            prompt = FAILURE_ANALYSIS_PROMPT.format(
                skill_name=skill.name,
                steps_text=steps_text,
                user_message=user_message,
                error_info=error_info[:500],
                failed_step=failed_step,
            )
            response = await model_router.complete_with_failover(
                messages=[Message(role="user", content=prompt)],
                user_message=prompt,
                temperature=0.2,
            )
            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]

            result = json.loads(content.strip())
            record.root_cause = result.get("root_cause", "")
            record.severity = result.get("severity", "minor")

            # 如果 LLM 建议更新步骤，自动应用
            if result.get("should_update_steps") and result.get("updated_steps"):
                await self.refine_skill(
                    skill_id=skill_id,
                    new_steps=result["updated_steps"],
                    reason=f"failure fix: {record.root_cause}",
                )

        except (ValueError, json.JSONDecodeError, KeyError) as e:
            logger.warning("Failure analysis failed: %s", e)

        return record

    async def refine_skill(
        self,
        skill_id: str,
        new_steps: Optional[list[dict]] = None,
        new_trigger: Optional[str] = None,
        reason: str = "",
    ) -> Optional[OptimizationResult]:
        """精炼技能 — 更新步骤/触发条件."""
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return None

        # 自动保存版本快照
        await self._skill_manager.save_version(skill_id, f"refine 前备份: {reason}")

        old_version = skill.version
        try:
            old_ver_int = int(old_version)
        except (ValueError, TypeError):
            old_ver_int = 0
        updates: dict[str, Any] = {}
        changes: list[str] = []

        if new_steps is not None:
            updates["steps"] = new_steps
            changes.append(f"steps updated ({len(new_steps)} steps)")
        if new_trigger is not None:
            updates["trigger"] = new_trigger
            changes.append("trigger updated")

        if not updates:
            return None

        await self._skill_manager.update_skill(skill_id, updates)
        logger.info("Refined skill %s: %s (reason: %s)", skill_id, changes, reason)

        return OptimizationResult(
            skill_id=skill_id,
            old_version=old_ver_int,
            new_version=old_ver_int + 1,
            changes=changes,
            success=True,
        )

    async def optimize_skill(
        self,
        skill_id: str,
        model_router: Optional[Any] = None,
    ) -> Optional[OptimizationResult]:
        """基于历史失败记录，用 LLM 全面优化技能步骤."""
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill or not model_router:
            return None

        # 自动保存版本快照
        await self._skill_manager.save_version(skill_id, "optimize 前备份")

        failures = self._failure_history.get(skill_id, [])
        if not failures:
            return None

        steps_text = "\n".join(
            f"  {i+1}. {s.get('description', '')} (tool: {s.get('tool', 'N/A')})"
            for i, s in enumerate(skill.steps)
        )
        failure_text = "\n".join(
            f"  - [{f.severity}] {f.root_cause or f.error_info[:100]}"
            for f in failures[-5:]
        )

        try:
            from agent.providers.base import Message
            prompt = STEP_OPTIMIZATION_PROMPT.format(
                skill_name=skill.name,
                steps_text=steps_text,
                failure_history=failure_text,
            )
            response = await model_router.complete_with_failover(
                messages=[Message(role="user", content=prompt)],
                user_message=prompt,
                temperature=0.3,
            )
            content = response.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[1] if "\n" in content else content[3:]
            if content.endswith("```"):
                content = content[:-3]

            new_steps = json.loads(content.strip())
            if isinstance(new_steps, list) and new_steps:
                return await self.refine_skill(
                    skill_id=skill_id,
                    new_steps=new_steps,
                    reason=f"optimized from {len(failures)} failures",
                )
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning("Skill optimization failed: %s", e)

        return None

    async def check_deprecation(self, skill_id: str) -> bool:
        """检查技能是否应该被废弃."""
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return False

        failure_count = self.get_failure_count(skill_id)
        should_deprecate = (
            failure_count >= self._max_failures
            or (skill.use_count >= 5 and skill.success_rate < self._min_success_rate)
        )

        if should_deprecate:
            await self._skill_manager.update_skill(skill_id, {
                "tags": list(set(skill.tags + ["deprecated"])),
            })
            logger.warning("Skill %s (%s) deprecated: %d failures, %.0f%% success",
                           skill_id, skill.name, failure_count, skill.success_rate * 100)

        return should_deprecate
