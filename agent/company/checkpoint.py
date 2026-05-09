"""CheckpointManager — Pipeline 检查点持久化与 interrupt/resume.

在关键阶段（PRD/UIDesign/Deploy）暂停 pipeline 等待用户确认，
状态持久化到磁盘，支持跨会话恢复。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_APPROVAL_STAGES = {"PRD", "UIDesign", "Deploy"}


@dataclass
class CheckpointConfig:
    """检查点配置."""

    approval_stages: set[str] = field(default_factory=lambda: _DEFAULT_APPROVAL_STAGES.copy())
    approval_timeout: float = 24 * 3600


class CheckpointManager:
    """Pipeline 检查点管理器."""

    def __init__(
        self, project_dir: Path, config: Optional[CheckpointConfig] = None
    ) -> None:
        self._project_dir = project_dir
        self._config = config or CheckpointConfig()
        self._checkpoint_file = project_dir / ".pipeline_checkpoint.json"

    @property
    def approval_stages(self) -> set[str]:
        return self._config.approval_stages

    @property
    def approval_timeout(self) -> float:
        return self._config.approval_timeout

    def needs_approval(self, stage_key: str) -> bool:
        """判断该阶段是否需要用户确认."""
        return stage_key in self._config.approval_stages

    def save(
        self,
        stages_done: dict[str, bool],
        stage_outputs: dict[str, str],
        requirement: str,
        task_id: str,
        waiting_stage: str = "",
    ) -> None:
        """持久化当前 pipeline 状态到磁盘."""
        data = {
            "stages_done": stages_done,
            "stage_outputs": {k: v[:5000] for k, v in stage_outputs.items()},
            "requirement": requirement[:10000],
            "task_id": task_id,
            "waiting_stage": waiting_stage,
            "saved_at": time.time(),
            "project_dir": str(self._project_dir),
        }
        try:
            self._checkpoint_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.debug("Checkpoint saved: waiting=%s", waiting_stage)
        except Exception as e:
            logger.warning("Checkpoint save failed: %s", e)

    def load(self) -> Optional[dict[str, Any]]:
        """从磁盘加载 checkpoint，返回 None 如果不存在或损坏."""
        if not self._checkpoint_file.exists():
            return None
        try:
            data = json.loads(self._checkpoint_file.read_text(encoding="utf-8"))
            if not isinstance(data.get("stages_done"), dict):
                return None
            return data
        except Exception as e:
            logger.warning("Checkpoint load failed: %s", e)
            return None

    def clear(self) -> None:
        """Pipeline 正常完成后清除 checkpoint."""
        try:
            self._checkpoint_file.unlink(missing_ok=True)
        except Exception:
            pass

    def is_waiting(self) -> Optional[str]:
        """检查是否有等待中的 approval（用于恢复）."""
        data = self.load()
        if data and data.get("waiting_stage"):
            return data["waiting_stage"]
        return None
