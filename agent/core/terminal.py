"""多终端后端 — 支持 Local/SSH/Docker/Tmux 执行环境.

用法:
    manager = TerminalManager()
    manager.register_backend("local", LocalBackend())
    result = await manager.execute("echo hello")
"""

from __future__ import annotations

import abc
import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class TerminalResult:
    """终端执行结果."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    backend: str = ""

class TerminalBackend(abc.ABC):
    """终端后端抽象基类."""

    @property
    @abc.abstractmethod
    def name(self) -> str: ...

    @abc.abstractmethod
    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult: ...

    async def is_available(self) -> bool:
        return True

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

class LocalBackend(TerminalBackend):
    """本地子进程后端."""

    @property
    def name(self) -> str:
        return "local"

    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return TerminalResult(
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    backend=self.name,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return TerminalResult(exit_code=-1, timed_out=True, backend=self.name)
        except Exception as e:
            return TerminalResult(stderr=str(e), exit_code=-1, backend=self.name)

class SSHBackend(TerminalBackend):
    """SSH 远程执行后端."""

    def __init__(
        self, host: str, port: int = 22,
        username: str = "root", key_path: Optional[str] = None,
        password: Optional[str] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.key_path = key_path
        self.password = password
        self._conn = None
        self._asyncssh_available: Optional[bool] = None

    @property
    def name(self) -> str:
        return f"ssh:{self.username}@{self.host}"

    async def is_available(self) -> bool:
        if self._asyncssh_available is None:
            try:
                import asyncssh  # noqa: F401
                self._asyncssh_available = True
            except ImportError:
                self._asyncssh_available = False
        return self._asyncssh_available

    async def connect(self) -> bool:
        try:
            import asyncssh
            kwargs: dict = {
                "host": self.host, "port": self.port,
                "username": self.username, "known_hosts": None,
            }
            if self.key_path:
                kwargs["client_keys"] = [self.key_path]
            if self.password:
                kwargs["password"] = self.password
            self._conn = await asyncssh.connect(**kwargs)
            return True
        except Exception as e:
            logger.error("SSH 连接失败: %s", e)
            return False

    async def disconnect(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult:
        if not self._conn:
            if not await self.connect():
                return TerminalResult(stderr="SSH 未连接", exit_code=-1, backend=self.name)
        try:
            cmd = f"cd {workdir} && {command}" if workdir else command
            result = await asyncio.wait_for(
                self._conn.run(cmd), timeout=timeout,
            )
            return TerminalResult(
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_status or 0,
                backend=self.name,
            )
        except asyncio.TimeoutError:
            return TerminalResult(exit_code=-1, timed_out=True, backend=self.name)
        except Exception as e:
            return TerminalResult(stderr=str(e), exit_code=-1, backend=self.name)

class DockerBackend(TerminalBackend):
    """Docker 容器执行后端."""

    def __init__(self, container_id: str) -> None:
        self.container_id = container_id

    @property
    def name(self) -> str:
        return f"docker:{self.container_id[:12]}"

    async def is_available(self) -> bool:
        return shutil.which("docker") is not None

    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult:
        args = ["docker", "exec"]
        if workdir:
            args += ["-w", workdir]
        args += [self.container_id, "sh", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return TerminalResult(
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    backend=self.name,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return TerminalResult(exit_code=-1, timed_out=True, backend=self.name)
        except Exception as e:
            return TerminalResult(stderr=str(e), exit_code=-1, backend=self.name)

class TmuxBackend(TerminalBackend):
    """Tmux 持久会话后端."""

    def __init__(self, session_name: str = "xjd-agent") -> None:
        self.session_name = session_name

    @property
    def name(self) -> str:
        return f"tmux:{self.session_name}"

    async def is_available(self) -> bool:
        return shutil.which("tmux") is not None

    async def connect(self) -> bool:
        """确保 tmux session 存在."""
        proc = await asyncio.create_subprocess_exec(
            "tmux", "has-session", "-t", self.session_name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            proc = await asyncio.create_subprocess_exec(
                "tmux", "new-session", "-d", "-s", self.session_name,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        return True

    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult:
        if not await self.connect():
            return TerminalResult(stderr="tmux session 创建失败", exit_code=-1, backend=self.name)

        # 用唯一标记包裹命令输出
        import uuid
        marker = f"__XJD_{uuid.uuid4().hex[:8]}__"
        full_cmd = command
        if workdir:
            full_cmd = f"cd {workdir} && {command}"
        wrapped = f'{full_cmd}; echo "{marker}$?"'

        # 发送命令
        proc = await asyncio.create_subprocess_exec(
            "tmux", "send-keys", "-t", self.session_name, wrapped, "Enter",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # 等待输出
        await asyncio.sleep(0.5)
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            capture = await asyncio.create_subprocess_exec(
                "tmux", "capture-pane", "-t", self.session_name, "-p",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await capture.communicate()
            output = stdout.decode("utf-8", errors="replace")
            if marker in output:
                lines = output.split("\n")
                result_lines = []
                exit_code = 0
                for line in lines:
                    if marker in line:
                        try:
                            exit_code = int(line.replace(marker, "").strip())
                        except ValueError:
                            pass
                        break
                    result_lines.append(line)
                return TerminalResult(
                    stdout="\n".join(result_lines),
                    exit_code=exit_code,
                    backend=self.name,
                )
            await asyncio.sleep(1)

        return TerminalResult(exit_code=-1, timed_out=True, backend=self.name)

class DaytonaBackend(TerminalBackend):
    """Daytona 开发环境后端 (通过 daytona CLI)."""

    def __init__(self, workspace: str = "") -> None:
        self.workspace = workspace

    @property
    def name(self) -> str:
        return f"daytona:{self.workspace}" if self.workspace else "daytona"

    async def is_available(self) -> bool:
        return shutil.which("daytona") is not None

    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult:
        args = ["daytona", "exec"]
        if self.workspace:
            args += [self.workspace, "--"]
        args += ["sh", "-c", command]
        if workdir:
            full_cmd = f"cd {workdir} && {command}"
            args = args[:-1] + [full_cmd]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return TerminalResult(
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    backend=self.name,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return TerminalResult(exit_code=-1, timed_out=True, backend=self.name)
        except Exception as e:
            return TerminalResult(stderr=str(e), exit_code=-1, backend=self.name)

class SingularityBackend(TerminalBackend):
    """Singularity/Apptainer 容器后端."""

    def __init__(self, image: str = "") -> None:
        self.image = image

    @property
    def name(self) -> str:
        return f"singularity:{self.image}" if self.image else "singularity"

    async def is_available(self) -> bool:
        return shutil.which("singularity") is not None or shutil.which("apptainer") is not None

    async def execute(
        self, command: str, workdir: Optional[str] = None, timeout: int = 120,
    ) -> TerminalResult:
        exe = "apptainer" if shutil.which("apptainer") else "singularity"
        args = [exe, "exec"]
        if workdir:
            args += ["--pwd", workdir]
        if self.image:
            args.append(self.image)
        else:
            args.append("library://default/default/ubuntu:latest")
        args += ["sh", "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                return TerminalResult(
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    backend=self.name,
                )
            except asyncio.TimeoutError:
                proc.kill()
                return TerminalResult(exit_code=-1, timed_out=True, backend=self.name)
        except Exception as e:
            return TerminalResult(stderr=str(e), exit_code=-1, backend=self.name)

class TerminalManager:
    """终端管理器 — 管理多个后端."""

    def __init__(self) -> None:
        self._backends: dict[str, TerminalBackend] = {}
        self._default: str = "local"
        # 自动注册本地后端
        self.register_backend(LocalBackend())

    def register_backend(self, backend: TerminalBackend) -> None:
        self._backends[backend.name] = backend

    def get_backend(self, name: str) -> Optional[TerminalBackend]:
        return self._backends.get(name)

    def list_backends(self) -> list[str]:
        return list(self._backends.keys())

    @property
    def default_backend(self) -> str:
        return self._default

    @default_backend.setter
    def default_backend(self, name: str) -> None:
        if name in self._backends:
            self._default = name

    async def execute(
        self, command: str, workdir: Optional[str] = None,
        timeout: int = 120, backend: Optional[str] = None,
    ) -> TerminalResult:
        name = backend or self._default
        b = self._backends.get(name)
        if not b:
            return TerminalResult(stderr=f"未知后端: {name}", exit_code=-1)
        return await b.execute(command, workdir, timeout)
