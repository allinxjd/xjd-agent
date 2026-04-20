"""Checkpoints & Rollback — 破坏性操作前自动快照，支持恢复.

功能:
- 在执行文件写入/删除等破坏性操作前自动创建快照
- 支持按 checkpoint ID 恢复到之前的状态
- 快照存储在 ~/.xjd-agent/checkpoints/ 目录
- 自动清理过期快照 (默认保留最近 20 个)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class FileSnapshot:
    """单个文件的快照."""
    path: str
    content: Optional[bytes] = None  # None = 文件不存在
    existed: bool = False


@dataclass
class Checkpoint:
    """检查点."""
    checkpoint_id: str = ""
    description: str = ""
    created_at: float = 0.0
    files: list[FileSnapshot] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def file_count(self) -> int:
        return len(self.files)


class CheckpointManager:
    """检查点管理器 — 自动快照 + 回滚."""

    def __init__(
        self,
        checkpoint_dir: str = "",
        max_checkpoints: int = 20,
    ) -> None:
        if not checkpoint_dir:
            from agent.core.config import get_home
            checkpoint_dir = str(get_home() / "checkpoints")
        self._dir = Path(checkpoint_dir)
        self._max = max_checkpoints
        self._checkpoints: list[Checkpoint] = []
        self._enabled = True

    def initialize(self) -> None:
        """初始化检查点目录."""
        self._dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    def create(self, files: list[str], description: str = "") -> Checkpoint:
        """创建检查点 — 快照指定文件的当前状态."""
        if not self._enabled:
            return Checkpoint()

        cp = Checkpoint(
            checkpoint_id=str(uuid.uuid4())[:8],
            description=description or f"checkpoint at {time.strftime('%H:%M:%S')}",
            created_at=time.time(),
        )

        for fpath in files:
            p = Path(fpath)
            snap = FileSnapshot(path=fpath, existed=p.exists())
            if p.exists() and p.is_file():
                try:
                    snap.content = p.read_bytes()
                except (OSError, PermissionError) as e:
                    logger.warning("无法快照 %s: %s", fpath, e)
            cp.files.append(snap)

        self._checkpoints.append(cp)
        self._save_checkpoint(cp)
        self._prune()
        logger.info("创建检查点 %s (%d 文件)", cp.checkpoint_id, cp.file_count)
        return cp

    def rollback(self, checkpoint_id: str = "") -> Optional[Checkpoint]:
        """回滚到指定检查点 (默认最近一个)."""
        if not self._checkpoints:
            return None

        if checkpoint_id:
            cp = next((c for c in self._checkpoints if c.checkpoint_id == checkpoint_id), None)
        else:
            cp = self._checkpoints[-1]

        if not cp:
            return None

        restored = 0
        for snap in cp.files:
            try:
                p = Path(snap.path)
                if snap.existed and snap.content is not None:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_bytes(snap.content)
                    restored += 1
                elif not snap.existed and p.exists():
                    p.unlink()
                    restored += 1
            except (OSError, PermissionError) as e:
                logger.error("回滚文件 %s 失败: %s", snap.path, e)

        logger.info("回滚到 %s: %d/%d 文件已恢复", cp.checkpoint_id, restored, cp.file_count)
        return cp

    def list_checkpoints(self) -> list[Checkpoint]:
        """列出所有检查点."""
        return list(self._checkpoints)

    def get_latest(self) -> Optional[Checkpoint]:
        """获取最近的检查点."""
        return self._checkpoints[-1] if self._checkpoints else None

    def clear(self) -> int:
        """清除所有检查点."""
        count = len(self._checkpoints)
        self._checkpoints.clear()
        if self._dir.exists():
            shutil.rmtree(self._dir)
            self._dir.mkdir(parents=True, exist_ok=True)
        return count

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def _save_checkpoint(self, cp: Checkpoint) -> None:
        """持久化检查点."""
        cp_dir = self._dir / cp.checkpoint_id
        cp_dir.mkdir(exist_ok=True)

        # 保存文件内容
        for i, snap in enumerate(cp.files):
            if snap.content is not None:
                (cp_dir / f"file_{i}.bin").write_bytes(snap.content)

        # 保存元数据
        meta = {
            "checkpoint_id": cp.checkpoint_id,
            "description": cp.description,
            "created_at": cp.created_at,
            "files": [
                {"path": s.path, "existed": s.existed, "has_content": s.content is not None}
                for s in cp.files
            ],
        }
        (cp_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

    def _load_index(self) -> None:
        """从磁盘加载检查点索引."""
        self._checkpoints.clear()
        if not self._dir.exists():
            return

        for cp_dir in sorted(self._dir.iterdir()):
            meta_file = cp_dir / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text())
                cp = Checkpoint(
                    checkpoint_id=meta["checkpoint_id"],
                    description=meta.get("description", ""),
                    created_at=meta.get("created_at", 0),
                )
                for i, fmeta in enumerate(meta.get("files", [])):
                    snap = FileSnapshot(path=fmeta["path"], existed=fmeta["existed"])
                    bin_file = cp_dir / f"file_{i}.bin"
                    if fmeta.get("has_content") and bin_file.exists():
                        snap.content = bin_file.read_bytes()
                    cp.files.append(snap)
                self._checkpoints.append(cp)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("加载检查点 %s 失败: %s", cp_dir.name, e)

    def _prune(self) -> None:
        """清理过期检查点."""
        while len(self._checkpoints) > self._max:
            old = self._checkpoints.pop(0)
            old_dir = self._dir / old.checkpoint_id
            if old_dir.exists():
                shutil.rmtree(old_dir)
