"""端口管理器 — AI Company 项目端口自动分配与回收."""

from __future__ import annotations

import json
import logging
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_BASE_PORT = 8100
_DEFAULT_MAX_PORT = 8199


@dataclass
class PortManager:
    """管理 AI Company 项目的端口分配."""

    base_port: int = _DEFAULT_BASE_PORT
    max_port: int = _DEFAULT_MAX_PORT
    _state_file: Path = field(default_factory=lambda: Path.home() / ".xjd-agent" / "company_ports.json")
    _allocated: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load()

    def _load(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._allocated = data.get("ports", {})
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("端口状态文件读取失败: %s", e)

    def _save(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps({"ports": self._allocated}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_port_available(self, port: int) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.bind(("0.0.0.0", port))
                return True
        except OSError:
            return False

    def allocate(self, project_name: str) -> int:
        """为项目分配端口。如果已分配则返回已有端口。"""
        if project_name in self._allocated:
            port = self._allocated[project_name]
            logger.info("项目 %s 已分配端口 %d", project_name, port)
            return port

        used_ports = set(self._allocated.values())
        for port in range(self.base_port, self.max_port + 1):
            if port in used_ports:
                continue
            if self._is_port_available(port):
                self._allocated[project_name] = port
                self._save()
                logger.info("为项目 %s 分配端口 %d", project_name, port)
                return port

        raise RuntimeError(f"端口范围 {self.base_port}-{self.max_port} 已耗尽")

    def release(self, project_name: str) -> None:
        """释放项目端口。"""
        if project_name in self._allocated:
            port = self._allocated.pop(project_name)
            self._save()
            logger.info("释放项目 %s 的端口 %d", project_name, port)

    def get(self, project_name: str) -> Optional[int]:
        """获取项目已分配的端口。"""
        return self._allocated.get(project_name)

    def list_all(self) -> dict[str, int]:
        """列出所有已分配的端口。"""
        return dict(self._allocated)
