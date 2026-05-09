"""PipelineStateMachine — 流水线状态机.

将 company.py 主循环中的阶段转换逻辑抽象为状态机，
提供清晰的状态查询和转换接口。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class StageTransition:
    """一次阶段转换记录."""
    from_stage: str
    to_stage: str
    trigger: str  # "completed" | "rework" | "skip" | "degrade" | "stuck"
    round_num: int = 0


class PipelineStateMachine:
    """管理 pipeline 阶段状态转换."""

    def __init__(self, stage_keys: list[str], skip_stages: Optional[set[str]] = None) -> None:
        self._stage_keys = list(stage_keys)
        self._stages_done: dict[str, bool] = {k: False for k in stage_keys}
        if skip_stages:
            for s in skip_stages:
                if s in self._stages_done:
                    self._stages_done[s] = True
        self._transitions: list[StageTransition] = []
        self._current_round: int = 0
        self._on_change: Optional[object] = None

    @property
    def stages_done(self) -> dict[str, bool]:
        return self._stages_done

    @property
    def is_complete(self) -> bool:
        return all(self._stages_done.values())

    @property
    def done_count(self) -> int:
        return sum(1 for v in self._stages_done.values() if v)

    @property
    def total_stages(self) -> int:
        return len(self._stages_done)

    @property
    def current_stage(self) -> Optional[str]:
        """下一个未完成的阶段."""
        for k in self._stage_keys:
            if not self._stages_done[k]:
                return k
        return None

    @property
    def completed_stages(self) -> list[str]:
        return [k for k in self._stage_keys if self._stages_done[k]]

    @property
    def pending_stages(self) -> list[str]:
        return [k for k in self._stage_keys if not self._stages_done[k]]

    @property
    def transitions(self) -> list[StageTransition]:
        return list(self._transitions)

    def complete(self, stage: str) -> None:
        """标记阶段完成."""
        if stage not in self._stages_done:
            logger.warning("尝试完成未知阶段: %s", stage)
            return
        prev = self.current_stage or stage
        self._stages_done[stage] = True
        self._transitions.append(StageTransition(
            from_stage=prev, to_stage=self.current_stage or "DONE",
            trigger="completed", round_num=self._current_round,
        ))
        if self._on_change:
            self._on_change(stage, "done")  # type: ignore[operator]

    def reset(self, stage: str) -> None:
        """重置阶段为未完成（返工时用）."""
        if stage in self._stages_done:
            self._stages_done[stage] = False
            self._transitions.append(StageTransition(
                from_stage=stage, to_stage=stage,
                trigger="rework", round_num=self._current_round,
            ))
            if self._on_change:
                self._on_change(stage, "running")  # type: ignore[operator]

    def reset_downstream(self, from_stage: str) -> list[str]:
        """重置某阶段之后的所有阶段（级联返工）."""
        reset_list = []
        found = False
        for k in self._stage_keys:
            if k == from_stage:
                found = True
                continue
            if found and self._stages_done[k]:
                self._stages_done[k] = False
                reset_list.append(k)
        if reset_list:
            self._transitions.append(StageTransition(
                from_stage=from_stage, to_stage=reset_list[0],
                trigger="rework", round_num=self._current_round,
            ))
        return reset_list

    def skip(self, stage: str) -> None:
        """跳过阶段（验证失败降级时用）."""
        if stage in self._stages_done:
            self._stages_done[stage] = True
            self._transitions.append(StageTransition(
                from_stage=stage, to_stage=self.current_stage or "DONE",
                trigger="skip", round_num=self._current_round,
            ))

    def force_pass(self, stages: list[str]) -> None:
        """强制通过多个阶段（stuck 时用）."""
        for s in stages:
            if s in self._stages_done:
                self._stages_done[s] = True
        self._transitions.append(StageTransition(
            from_stage=stages[0] if stages else "?",
            to_stage=self.current_stage or "DONE",
            trigger="stuck", round_num=self._current_round,
        ))

    def reset_all(self) -> None:
        """全部重置（新迭代时用）."""
        for k in self._stages_done:
            self._stages_done[k] = False
        self._transitions.clear()

    def set_round(self, n: int) -> None:
        self._current_round = n

    def get_progress_label(self, stage_labels: dict[str, str], action_name: str = "") -> str:
        """生成进度标签，如 [3/10] 技术方案..."""
        label = stage_labels.get(action_name, "")
        if not label and self.current_stage:
            label = stage_labels.get(self.current_stage, "工作中")
        return f"[{self.done_count + 1}/{self.total_stages}] {label}..."

    def summary(self) -> str:
        """调试用摘要."""
        done = [k for k, v in self._stages_done.items() if v]
        pending = [k for k, v in self._stages_done.items() if not v]
        return f"done={done} pending={pending}"
