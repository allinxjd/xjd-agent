"""执行沙箱 — 隔离工具执行环境 (支持 3 种沙箱模式:
- NONE: 无隔离 (默认)
- SUBPROCESS: 子进程隔离 (ulimit 限制资源)
- DOCKER: Docker 容器隔离 (最安全)

用法:
    config = SandboxConfig(sandbox_type=SandboxType.DOCKER)
    executor = SandboxExecutor(config)
    result = await executor.execute("python script.py")
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

class SandboxType(str, Enum):
    """沙箱类型."""
    NONE = "none"
    SUBPROCESS = "subprocess"
    DOCKER = "docker"

@dataclass
class SandboxConfig:
    """沙箱配置."""
    sandbox_type: SandboxType = SandboxType.NONE
    docker_image: str = "python:3.12-slim"
    timeout: int = 30  # 秒
    memory_limit: str = "512m"  # Docker 内存限制
    cpu_limit: float = 1.0  # CPU 核数限制
    network_enabled: bool = False  # 是否允许网络
    allowed_paths: list[str] = field(default_factory=list)  # 允许挂载的路径
    work_dir: str = ""  # 工作目录

@dataclass
class SandboxResult:
    """沙箱执行结果."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    error: str = ""

class SandboxExecutor:
    """沙箱执行器.

    根据配置选择隔离策略执行命令。
    """

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self._config = config or SandboxConfig()
        self._container_ids: list[str] = []

    @property
    def sandbox_type(self) -> SandboxType:
        return self._config.sandbox_type

    def is_available(self) -> bool:
        """检查沙箱是否可用."""
        if self._config.sandbox_type == SandboxType.NONE:
            return True
        elif self._config.sandbox_type == SandboxType.SUBPROCESS:
            return True
        elif self._config.sandbox_type == SandboxType.DOCKER:
            return shutil.which("docker") is not None
        return False

    async def execute(
        self,
        command: str,
        cwd: str = "",
        env: Optional[dict[str, str]] = None,
    ) -> SandboxResult:
        """在沙箱中执行命令."""
        if self._config.sandbox_type == SandboxType.NONE:
            return await self._execute_direct(command, cwd, env)
        elif self._config.sandbox_type == SandboxType.SUBPROCESS:
            return await self._execute_subprocess(command, cwd, env)
        elif self._config.sandbox_type == SandboxType.DOCKER:
            return await self._execute_docker(command, cwd, env)
        return SandboxResult(error=f"未知沙箱类型: {self._config.sandbox_type}")

    async def _execute_direct(
        self, command: str, cwd: str, env: Optional[dict],
    ) -> SandboxResult:
        """直接执行 (无隔离)."""
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd or None,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._config.timeout,
                )
                return SandboxResult(
                    stdout=stdout.decode(errors="replace")[:20000],
                    stderr=stderr.decode(errors="replace")[:5000],
                    exit_code=proc.returncode or 0,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(timed_out=True, error=f"超时 ({self._config.timeout}s)")
        except Exception as e:
            return SandboxResult(error=str(e))

    async def _execute_subprocess(
        self, command: str, cwd: str, env: Optional[dict],
    ) -> SandboxResult:
        """子进程隔离 (ulimit 限制)."""
        # 用 ulimit 限制资源
        mem_kb = _parse_memory(self._config.memory_limit)
        wrapped = (
            f"ulimit -v {mem_kb} -t {self._config.timeout} 2>/dev/null; "
            f"{command}"
        )
        return await self._execute_direct(wrapped, cwd, env)

    async def _execute_docker(
        self, command: str, cwd: str, env: Optional[dict],
    ) -> SandboxResult:
        """Docker 容器隔离."""
        if not shutil.which("docker"):
            return SandboxResult(error="Docker 未安装或不在 PATH 中")

        # 构建 docker run 命令
        docker_cmd = [
            "docker", "run", "--rm",
            "--memory", self._config.memory_limit,
            f"--cpus={self._config.cpu_limit}",
            "--pids-limit", "100",
        ]

        if not self._config.network_enabled:
            docker_cmd.append("--network=none")

        # 挂载工作目录
        work = cwd or self._config.work_dir
        if work:
            docker_cmd.extend(["-v", f"{work}:/workspace", "-w", "/workspace"])

        # 挂载允许的路径 (只读)
        for path in self._config.allowed_paths:
            docker_cmd.extend(["-v", f"{path}:{path}:ro"])

        # 环境变量
        if env:
            for k, v in env.items():
                docker_cmd.extend(["-e", f"{k}={v}"])

        docker_cmd.extend([self._config.docker_image, "sh", "-c", command])

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=self._config.timeout + 10,
                )
                return SandboxResult(
                    stdout=stdout.decode(errors="replace")[:20000],
                    stderr=stderr.decode(errors="replace")[:5000],
                    exit_code=proc.returncode or 0,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return SandboxResult(timed_out=True, error=f"Docker 执行超时 ({self._config.timeout}s)")
        except Exception as e:
            return SandboxResult(error=f"Docker 执行失败: {e}")

    async def cleanup(self) -> None:
        """清理所有容器."""
        for cid in self._container_ids:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", cid,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except Exception:
                pass
        self._container_ids.clear()

def _parse_memory(mem_str: str) -> int:
    """解析内存字符串为 KB."""
    mem_str = mem_str.strip().lower()
    if mem_str.endswith("g"):
        return int(float(mem_str[:-1]) * 1024 * 1024)
    elif mem_str.endswith("m"):
        return int(float(mem_str[:-1]) * 1024)
    elif mem_str.endswith("k"):
        return int(float(mem_str[:-1]))
    return int(mem_str)
