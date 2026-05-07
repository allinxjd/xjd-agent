"""ProjectContext — 流水线项目状态容器.

封装单个项目在 pipeline 执行期间的所有状态，替代 company.py 中散落的局部变量。
支持"包装模式"：传入已有的 stages_done/stage_outputs 字典，共享引用，
使得现有代码无需一次性全改。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProjectContext:
    """Pipeline 运行期间的项目上下文."""

    name: str
    directory: Optional[Path] = None
    requirement: str = ""

    stages_done: dict[str, bool] = field(default_factory=dict)
    stage_outputs: dict[str, str] = field(default_factory=dict)
    rework_counts: dict[str, int] = field(default_factory=dict)
    rework_feedbacks: dict[str, list[str]] = field(default_factory=dict)

    tech_stack: str = ""
    port: Optional[int] = None
    status: str = "in_progress"

    @classmethod
    def wrap(
        cls,
        name: str,
        stages_done: dict[str, bool],
        stage_outputs: dict[str, str],
        rework_counts: dict[str, int],
        directory: Optional[Path] = None,
        requirement: str = "",
    ) -> "ProjectContext":
        """包装已有字典创建 context（共享引用，不复制）."""
        ctx = cls.__new__(cls)
        ctx.name = name
        ctx.directory = directory
        ctx.requirement = requirement
        ctx.stages_done = stages_done
        ctx.stage_outputs = stage_outputs
        ctx.rework_counts = rework_counts
        ctx.rework_feedbacks = {}
        ctx.port = None
        ctx.status = "in_progress"
        ctx.tech_stack = ""
        if directory and directory.exists():
            ctx.tech_stack = ctx._detect_tech_stack()
        return ctx

    def __post_init__(self) -> None:
        if self.directory and not self.tech_stack:
            self.tech_stack = self._detect_tech_stack()

    @property
    def total_stages(self) -> int:
        return len(self.stages_done)

    @property
    def done_count(self) -> int:
        return sum(1 for v in self.stages_done.values() if v)

    @property
    def current_stage(self) -> Optional[str]:
        """返回下一个未完成的阶段 key."""
        for k, v in self.stages_done.items():
            if not v:
                return k
        return None

    @property
    def is_complete(self) -> bool:
        return all(self.stages_done.values())

    def mark_done(self, stage: str, output: str = "") -> None:
        self.stages_done[stage] = True
        if output:
            self.stage_outputs[stage] = output
        self._save_checkpoint()

    def mark_undone(self, stage: str) -> None:
        self.stages_done[stage] = False
        self.stage_outputs.pop(stage, None)

    def record_rework(self, stage: str, feedback: str) -> int:
        """记录返工，返回该阶段累计返工次数."""
        self.rework_counts[stage] = self.rework_counts.get(stage, 0) + 1
        if stage not in self.rework_feedbacks:
            self.rework_feedbacks[stage] = []
        self.rework_feedbacks[stage].append(feedback)
        return self.rework_counts[stage]

    def can_rework(self, stage: str, max_rework: int = 3) -> bool:
        return self.rework_counts.get(stage, 0) < max_rework

    # ── 持久化 ──

    def _save_checkpoint(self) -> None:
        if not self.directory:
            return
        meta_file = self.directory / ".project.json"
        meta: dict = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        meta["name"] = self.name
        meta["status"] = self.status
        meta["stages_done"] = self.stages_done
        meta["stage_outputs"] = self.stage_outputs
        if self.port:
            meta["port"] = self.port
        try:
            meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        except OSError as e:
            logger.warning("保存 checkpoint 失败: %s", e)

    @classmethod
    def load_from_directory(cls, directory: Path, stage_keys: list[str]) -> Optional["ProjectContext"]:
        """从 .project.json 恢复上下文."""
        meta_file = directory / ".project.json"
        if not meta_file.exists():
            return None
        try:
            meta = json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            return None

        name = meta.get("name", directory.name)
        stages_done = {k: False for k in stage_keys}
        saved = meta.get("stages_done", {})
        for k in stages_done:
            if saved.get(k, False):
                stages_done[k] = True

        ctx = cls(
            name=name,
            directory=directory,
            stages_done=stages_done,
            stage_outputs=meta.get("stage_outputs", {}),
            port=meta.get("port"),
            status=meta.get("status", "in_progress"),
        )
        return ctx

    # ── Tech Stack ──

    def _detect_tech_stack(self) -> str:
        """检测项目技术栈（轻量版，供 context 注入用）."""
        if not self.directory or not self.directory.exists():
            return ""
        from agent.company.company import Company
        return Company._extract_tech_stack(self.directory)

    @property
    def code_manager(self) -> "CodeManager":
        """懒加载 CodeManager 实例."""
        if not hasattr(self, "_code_manager") or self._code_manager is None:
            from agent.company.code_manager import CodeManager
            if self.directory and self.directory.exists():
                self._code_manager = CodeManager(self.directory)
            else:
                self._code_manager = None
        return self._code_manager

    def get_context_summary(self) -> str:
        """生成供 LLM prompt 注入的项目摘要."""
        parts = [f"项目：{self.name}"]
        if self.directory:
            parts.append(f"目录：{self.directory}")
        if self.tech_stack:
            parts.append(f"技术栈：{self.tech_stack}")
        progress = f"进度：{self.done_count}/{self.total_stages}"
        if self.current_stage:
            progress += f"（当前：{self.current_stage}）"
        parts.append(progress)
        return "\n".join(parts)
