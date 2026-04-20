"""技能评估器 — 评估技能效果，推荐废弃/优化.

核心能力:
1. 效果评分 — 综合使用次数、成功率、最近表现
2. 废弃推荐 — 识别低效技能
3. 优化推荐 — 识别可改进的技能
4. 统计报告 — 技能系统整体健康度
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class SkillScore:
    """技能评分."""
    skill_id: str = ""
    name: str = ""
    effectiveness: float = 0.0  # 0-1, 综合评分
    usage_score: float = 0.0    # 使用频率得分
    success_score: float = 0.0  # 成功率得分
    recency_score: float = 0.0  # 最近活跃度
    recommendation: str = ""    # keep / optimize / deprecate

@dataclass
class SystemHealth:
    """技能系统健康报告."""
    total_skills: int = 0
    active_skills: int = 0
    deprecated_skills: int = 0
    avg_success_rate: float = 0.0
    avg_effectiveness: float = 0.0
    top_skills: list[str] = field(default_factory=list)
    needs_attention: list[str] = field(default_factory=list)

class SkillEvaluator:
    """技能评估器."""

    def __init__(
        self,
        skill_manager: Any,
        optimizer: Optional[Any] = None,
    ) -> None:
        self._skill_manager = skill_manager
        self._optimizer = optimizer

    async def score_skill(self, skill_id: str) -> Optional[SkillScore]:
        """评估单个技能的效果."""
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return None

        # 使用频率得分 (log scale, cap at 50 uses)
        import math
        usage_score = min(math.log(skill.use_count + 1) / math.log(51), 1.0)

        # 成功率得分
        success_score = skill.success_rate

        # 最近活跃度 (7天内更新 = 1.0, 30天 = 0.5, 更久 = 0.1)
        days_since_update = (time.time() - skill.updated_at) / 86400 if skill.updated_at else 999
        if days_since_update <= 7:
            recency_score = 1.0
        elif days_since_update <= 30:
            recency_score = 0.5
        else:
            recency_score = 0.1

        # 综合评分 (加权)
        effectiveness = (
            usage_score * 0.3
            + success_score * 0.5
            + recency_score * 0.2
        )

        # 推荐
        is_deprecated = "deprecated" in skill.tags
        if is_deprecated or effectiveness < 0.2:
            recommendation = "deprecate"
        elif effectiveness < 0.5 or success_score < 0.6:
            recommendation = "optimize"
        else:
            recommendation = "keep"

        return SkillScore(
            skill_id=skill_id,
            name=skill.name,
            effectiveness=round(effectiveness, 3),
            usage_score=round(usage_score, 3),
            success_score=round(success_score, 3),
            recency_score=round(recency_score, 3),
            recommendation=recommendation,
        )

    async def evaluate_all(self) -> list[SkillScore]:
        """评估所有技能."""
        skills = await self._skill_manager.list_skills()
        scores = []
        for skill in skills:
            score = await self.score_skill(skill.skill_id)
            if score:
                scores.append(score)
        return sorted(scores, key=lambda s: s.effectiveness, reverse=True)

    async def get_system_health(self) -> SystemHealth:
        """获取技能系统整体健康报告."""
        scores = await self.evaluate_all()
        if not scores:
            return SystemHealth()

        active = [s for s in scores if s.recommendation != "deprecate"]
        deprecated = [s for s in scores if s.recommendation == "deprecate"]
        needs_opt = [s for s in scores if s.recommendation == "optimize"]

        return SystemHealth(
            total_skills=len(scores),
            active_skills=len(active),
            deprecated_skills=len(deprecated),
            avg_success_rate=sum(s.success_score for s in scores) / len(scores),
            avg_effectiveness=sum(s.effectiveness for s in scores) / len(scores),
            top_skills=[s.name for s in scores[:5]],
            needs_attention=[s.name for s in needs_opt],
        )

    async def auto_maintain(
        self,
        model_router: Optional[Any] = None,
    ) -> dict[str, Any]:
        """自动维护 — 废弃低效技能，优化可改进技能."""
        report: dict[str, Any] = {"deprecated": [], "optimized": [], "kept": []}
        scores = await self.evaluate_all()

        for score in scores:
            if score.recommendation == "deprecate":
                if self._optimizer:
                    await self._optimizer.check_deprecation(score.skill_id)
                report["deprecated"].append(score.name)
            elif score.recommendation == "optimize" and self._optimizer and model_router:
                result = await self._optimizer.optimize_skill(score.skill_id, model_router)
                if result and result.success:
                    report["optimized"].append(score.name)
                else:
                    report["kept"].append(score.name)
            else:
                report["kept"].append(score.name)

        logger.info(
            "Auto-maintain: %d deprecated, %d optimized, %d kept",
            len(report["deprecated"]), len(report["optimized"]), len(report["kept"]),
        )
        return report
