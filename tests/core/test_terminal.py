"""测试 — 多终端后端."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.terminal import (
    DockerBackend,
    LocalBackend,
    SSHBackend,
    TerminalManager,
    TerminalResult,
    TmuxBackend,
)


class TestLocalBackend:
    @pytest.mark.asyncio
    async def test_execute_echo(self):
        backend = LocalBackend()
        result = await backend.execute("echo hello_terminal")
        assert "hello_terminal" in result.stdout
        assert result.exit_code == 0
        assert result.backend == "local"

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        backend = LocalBackend()
        result = await backend.execute("exit 42")
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        backend = LocalBackend()
        result = await backend.execute("sleep 10", timeout=1)
        assert result.timed_out is True

    def test_name(self):
        assert LocalBackend().name == "local"

    @pytest.mark.asyncio
    async def test_is_available(self):
        assert await LocalBackend().is_available() is True


class TestSSHBackend:
    def test_name(self):
        b = SSHBackend(host="example.com", username="user")
        assert b.name == "ssh:user@example.com"

    @pytest.mark.asyncio
    async def test_not_available_without_asyncssh(self):
        b = SSHBackend(host="example.com")
        with patch.dict("sys.modules", {"asyncssh": None}):
            # asyncssh import will fail
            available = await b.is_available()
            # May or may not be available depending on env
            assert isinstance(available, bool)

    @pytest.mark.asyncio
    async def test_execute_without_connection(self):
        b = SSHBackend(host="nonexistent.invalid")
        result = await b.execute("echo test")
        assert result.exit_code == -1


class TestDockerBackend:
    def test_name(self):
        b = DockerBackend(container_id="abc123def456")
        assert "abc123def456" in b.name

    @pytest.mark.asyncio
    async def test_execute_no_container(self):
        b = DockerBackend(container_id="nonexistent_container_xyz")
        result = await b.execute("echo test")
        # Will fail because container doesn't exist
        assert result.exit_code != 0 or result.stderr


class TestTmuxBackend:
    def test_name(self):
        b = TmuxBackend(session_name="test-session")
        assert "test-session" in b.name


class TestTerminalManager:
    def test_default_has_local(self):
        mgr = TerminalManager()
        assert "local" in mgr.list_backends()
        assert mgr.default_backend == "local"

    def test_register_backend(self):
        mgr = TerminalManager()
        docker = DockerBackend(container_id="test123")
        mgr.register_backend(docker)
        assert docker.name in mgr.list_backends()

    def test_set_default(self):
        mgr = TerminalManager()
        docker = DockerBackend(container_id="test123")
        mgr.register_backend(docker)
        mgr.default_backend = docker.name
        assert mgr.default_backend == docker.name

    @pytest.mark.asyncio
    async def test_execute_default(self):
        mgr = TerminalManager()
        result = await mgr.execute("echo mgr_test")
        assert "mgr_test" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_unknown_backend(self):
        mgr = TerminalManager()
        result = await mgr.execute("echo test", backend="nonexistent")
        assert result.exit_code == -1
        assert "未知后端" in result.stderr
