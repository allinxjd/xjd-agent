"""项目管理器 — AI Company 项目生命周期管理."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from agent.company.port_manager import PortManager

logger = logging.getLogger(__name__)

_PROJECTS_ROOT = Path.home() / "xjd-projects"


@dataclass
class ProjectInfo:
    """项目状态信息."""

    name: str
    path: str
    port: Optional[int] = None
    pid: Optional[int] = None
    status: str = "unknown"
    created_at: str = ""
    deploy_url: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class ProjectManager:
    """管理 AI Company 创建的项目."""

    def __init__(self, port_manager: Optional[PortManager] = None) -> None:
        self._port_manager = port_manager or PortManager()
        self._projects_root = _PROJECTS_ROOT

    def list_projects(self) -> list[ProjectInfo]:
        """列出所有项目。"""
        projects: list[ProjectInfo] = []
        if not self._projects_root.exists():
            return projects

        for entry in sorted(self._projects_root.iterdir()):
            if not entry.is_dir():
                continue
            info = self._get_project_info(entry)
            if info:
                projects.append(info)
        return projects

    def get_status(self, name: str) -> Optional[ProjectInfo]:
        """获取单个项目状态。"""
        project_dir = self._find_project_dir(name)
        if not project_dir:
            return None
        return self._get_project_info(project_dir)

    def stop_project(self, name: str) -> bool:
        """停止项目进程。"""
        project_dir = self._find_project_dir(name)
        if not project_dir:
            return False

        pid = self._read_pid(project_dir)
        if pid and self._is_process_running(pid):
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(1)
                if self._is_process_running(pid):
                    os.kill(pid, signal.SIGKILL)
                logger.info("已停止项目 %s (PID %d)", name, pid)
                return True
            except OSError as e:
                logger.warning("停止项目 %s 失败: %s", name, e)
                return False

        port = self._read_port(project_dir)
        if port:
            return self._kill_by_port(port)
        return False

    def restart_project(self, name: str) -> bool:
        """重启项目。"""
        project_dir = self._find_project_dir(name)
        if not project_dir:
            return False

        self.stop_project(name)
        time.sleep(1)
        return self._start_project(project_dir)

    def cleanup_project(self, name: str) -> bool:
        """清理项目（停止进程 + 释放端口 + 可选删除目录）。"""
        self.stop_project(name)
        self._port_manager.release(name)
        logger.info("已清理项目 %s 资源", name)
        return True

    def backup_before_deploy(self, name: str) -> Optional[Path]:
        """部署前备份项目目录。"""
        project_dir = self._find_project_dir(name)
        if not project_dir:
            return None

        backup_dir = project_dir / ".backups"
        backup_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = backup_dir / f"{ts}.tar.gz"

        try:
            subprocess.run(
                ["tar", "czf", str(backup_path),
                 "--exclude=.backups", "--exclude=node_modules",
                 "--exclude=__pycache__", "--exclude=.venv",
                 "-C", str(project_dir.parent), project_dir.name],
                check=True, capture_output=True, timeout=60,
            )
            logger.info("项目 %s 备份到 %s", name, backup_path)
            return backup_path
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning("备份项目 %s 失败: %s", name, e)
            return None

    def rollback(self, name: str) -> bool:
        """回滚到最近一次备份。"""
        project_dir = self._find_project_dir(name)
        if not project_dir:
            return False

        backup_dir = project_dir / ".backups"
        if not backup_dir.exists():
            logger.warning("项目 %s 没有备份", name)
            return False

        backups = sorted(backup_dir.glob("*.tar.gz"), reverse=True)
        if not backups:
            return False

        self.stop_project(name)
        latest = backups[0]
        try:
            subprocess.run(
                ["tar", "xzf", str(latest), "-C", str(project_dir.parent)],
                check=True, capture_output=True, timeout=60,
            )
            logger.info("项目 %s 已回滚到 %s", name, latest.name)
            self._start_project(project_dir)
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.warning("回滚项目 %s 失败: %s", name, e)
            return False

    def list_backups(self, name: str) -> list[str]:
        """列出项目的所有备份。"""
        project_dir = self._find_project_dir(name)
        if not project_dir:
            return []
        backup_dir = project_dir / ".backups"
        if not backup_dir.exists():
            return []
        return [b.name for b in sorted(backup_dir.glob("*.tar.gz"), reverse=True)]

    # ── 私有方法 ──

    def _find_project_dir(self, name: str) -> Optional[Path]:
        if not self._projects_root.exists():
            return None
        for entry in self._projects_root.iterdir():
            if entry.is_dir() and entry.name.endswith(f"-{name}"):
                return entry
            if entry.is_dir() and entry.name == name:
                return entry
        return None

    def _get_project_info(self, project_dir: Path) -> ProjectInfo:
        name_parts = project_dir.name.split("-", 1)
        display_name = name_parts[1] if len(name_parts) > 1 else project_dir.name

        port = self._read_port(project_dir)
        pid = self._read_pid(project_dir)
        status = "stopped"

        if pid and self._is_process_running(pid):
            status = "running"
        elif port and self._is_port_in_use(port):
            status = "running"

        deploy_url = None
        if status == "running" and port:
            deploy_url = f"http://localhost:{port}"

        stat = project_dir.stat()
        created_at = datetime.fromtimestamp(stat.st_ctime).strftime("%Y-%m-%d %H:%M")

        return ProjectInfo(
            name=display_name,
            path=str(project_dir),
            port=port,
            pid=pid,
            status=status,
            created_at=created_at,
            deploy_url=deploy_url,
        )

    def _read_port(self, project_dir: Path) -> Optional[int]:
        port_file = project_dir / ".port"
        if port_file.exists():
            try:
                return int(port_file.read_text().strip())
            except (ValueError, OSError):
                pass
        return self._port_manager.get(project_dir.name.split("-", 1)[-1])

    def _read_pid(self, project_dir: Path) -> Optional[int]:
        pid_file = project_dir / ".pid"
        if pid_file.exists():
            try:
                return int(pid_file.read_text().strip())
            except (ValueError, OSError):
                pass
        return None

    @staticmethod
    def _is_process_running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _is_port_in_use(port: int) -> bool:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.bind(("0.0.0.0", port))
                return False
        except OSError:
            return True

    def _kill_by_port(self, port: int) -> bool:
        try:
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5,
            )
            if result.stdout.strip():
                for pid_str in result.stdout.strip().split("\n"):
                    try:
                        os.kill(int(pid_str), signal.SIGTERM)
                    except (ValueError, OSError):
                        pass
                logger.info("已通过端口 %d 停止进程", port)
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return False

    def _start_project(self, project_dir: Path) -> bool:
        start_script = project_dir / "start.sh"
        if not start_script.exists():
            entry = self._detect_entry(project_dir)
            if not entry:
                logger.warning("项目 %s 没有启动脚本或入口文件", project_dir.name)
                return False
            cmd_parts = entry.split()
        else:
            cmd_parts = ["bash", str(start_script)]

        log_file = project_dir / "app.log"
        pid_file = project_dir / ".pid"

        try:
            with open(log_file, "a") as lf:
                proc = subprocess.Popen(
                    cmd_parts,
                    cwd=str(project_dir),
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            pid_file.write_text(str(proc.pid))
            logger.info("已启动项目 %s (PID %d)", project_dir.name, proc.pid)
            return True
        except OSError as e:
            logger.warning("启动项目 %s 失败: %s", project_dir.name, e)
            return False

    @staticmethod
    def _detect_entry(project_dir: Path) -> Optional[str]:
        for name, cmd in [
            ("main.py", "python3 main.py"),
            ("app.py", "python3 app.py"),
            ("index.js", "node index.js"),
            ("server.js", "node server.js"),
            ("package.json", "npm start"),
        ]:
            if (project_dir / name).exists():
                return cmd
        return None
