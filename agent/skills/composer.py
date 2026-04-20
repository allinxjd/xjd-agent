"""技能组合器 — 将多个技能串联为流水线.

核心能力:
1. 技能链 — 按顺序执行多个技能
2. 条件分支 — 根据上一步结果选择下一个技能
3. 并行执行 — 独立技能并行运行
4. 管道模板 — 预定义常用组合
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

@dataclass
class PipelineStep:
    """流水线步骤."""
    skill_id: str = ""
    condition: str = ""  # 空 = 无条件执行; "success" / "failure" / 自定义表达式
    parallel: bool = False  # 是否与下一步并行

@dataclass
class SkillPipeline:
    """技能流水线."""
    pipeline_id: str = ""
    name: str = ""
    description: str = ""
    steps: list[PipelineStep] = field(default_factory=list)
    created_at: float = 0.0
    use_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "name": self.name,
            "description": self.description,
            "steps": [
                {"skill_id": s.skill_id, "condition": s.condition, "parallel": s.parallel}
                for s in self.steps
            ],
            "created_at": self.created_at,
            "use_count": self.use_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SkillPipeline:
        return cls(
            pipeline_id=data.get("pipeline_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            steps=[
                PipelineStep(
                    skill_id=s.get("skill_id", ""),
                    condition=s.get("condition", ""),
                    parallel=s.get("parallel", False),
                )
                for s in data.get("steps", [])
            ],
            created_at=data.get("created_at", 0),
            use_count=data.get("use_count", 0),
        )

class SkillComposer:
    """技能组合器."""

    def __init__(self, skill_manager: Any, pipelines_dir: Optional[str] = None) -> None:
        self._skill_manager = skill_manager
        if pipelines_dir:
            from pathlib import Path
            self._pipelines_dir = Path(pipelines_dir)
        else:
            from agent.core.config import get_skills_dir
            self._pipelines_dir = get_skills_dir() / "pipelines"
        self._pipelines: dict[str, SkillPipeline] = {}

    async def load_pipelines(self) -> int:
        """加载所有流水线."""
        self._pipelines_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for path in self._pipelines_dir.glob("*.yaml"):
            try:
                with open(path) as f:
                    data = yaml.safe_load(f) or {}
                pipeline = SkillPipeline.from_dict(data)
                if pipeline.pipeline_id:
                    self._pipelines[pipeline.pipeline_id] = pipeline
                    count += 1
            except Exception as e:
                logger.warning("Failed to load pipeline %s: %s", path.name, e)
        return count

    async def create_pipeline(
        self,
        name: str,
        description: str,
        skill_ids: list[str],
        conditions: Optional[list[str]] = None,
    ) -> SkillPipeline:
        """创建技能流水线."""
        import uuid
        steps = []
        for i, sid in enumerate(skill_ids):
            cond = conditions[i] if conditions and i < len(conditions) else ""
            steps.append(PipelineStep(skill_id=sid, condition=cond))

        pipeline = SkillPipeline(
            pipeline_id=str(uuid.uuid4())[:8],
            name=name,
            description=description,
            steps=steps,
            created_at=time.time(),
        )
        self._pipelines[pipeline.pipeline_id] = pipeline
        await self._save_pipeline(pipeline)
        logger.info("Created pipeline: %s (%d steps)", name, len(steps))
        return pipeline

    async def get_pipeline(self, pipeline_id: str) -> Optional[SkillPipeline]:
        return self._pipelines.get(pipeline_id)

    async def list_pipelines(self) -> list[SkillPipeline]:
        return list(self._pipelines.values())

    async def resolve_pipeline(self, pipeline_id: str) -> list[Any]:
        """解析流水线为有序技能列表 (展开所有步骤)."""
        pipeline = self._pipelines.get(pipeline_id)
        if not pipeline:
            return []

        skills = []
        for step in pipeline.steps:
            skill = await self._skill_manager.get_skill(step.skill_id)
            if skill:
                skills.append(skill)
        return skills

    async def delete_pipeline(self, pipeline_id: str) -> bool:
        pipeline = self._pipelines.pop(pipeline_id, None)
        if not pipeline:
            return False
        path = self._pipelines_dir / f"{pipeline_id}.yaml"
        if path.exists():
            path.unlink()
        return True

    async def _save_pipeline(self, pipeline: SkillPipeline) -> None:
        self._pipelines_dir.mkdir(parents=True, exist_ok=True)
        path = self._pipelines_dir / f"{pipeline.pipeline_id}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(pipeline.to_dict(), f, default_flow_style=False, allow_unicode=True)
