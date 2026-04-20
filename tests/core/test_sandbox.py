"""测试 — 执行沙箱."""

from __future__ import annotations

import pytest

from agent.core.sandbox import (
    SandboxConfig,
    SandboxExecutor,
    SandboxResult,
    SandboxType,
    _parse_memory,
)


class TestSandboxConfig:
    def test_defaults(self):
        cfg = SandboxConfig()
        assert cfg.sandbox_type == SandboxType.NONE
        assert cfg.timeout == 30
        assert cfg.memory_limit == "512m"
        assert cfg.network_enabled is False

    def test_docker_config(self):
        cfg = SandboxConfig(
            sandbox_type=SandboxType.DOCKER,
            docker_image="node:20-slim",
            memory_limit="1g",
        )
        assert cfg.sandbox_type == SandboxType.DOCKER
        assert cfg.docker_image == "node:20-slim"


class TestParseMemory:
    def test_megabytes(self):
        assert _parse_memory("512m") == 512 * 1024

    def test_gigabytes(self):
        assert _parse_memory("2g") == 2 * 1024 * 1024

    def test_kilobytes(self):
        assert _parse_memory("1024k") == 1024

    def test_raw_number(self):
        assert _parse_memory("65536") == 65536


class TestSandboxExecutor:
    def test_none_always_available(self):
        executor = SandboxExecutor(SandboxConfig(sandbox_type=SandboxType.NONE))
        assert executor.is_available() is True

    def test_subprocess_always_available(self):
        executor = SandboxExecutor(SandboxConfig(sandbox_type=SandboxType.SUBPROCESS))
        assert executor.is_available() is True

    def test_sandbox_type_property(self):
        executor = SandboxExecutor(SandboxConfig(sandbox_type=SandboxType.DOCKER))
        assert executor.sandbox_type == SandboxType.DOCKER

    @pytest.mark.asyncio
    async def test_execute_direct(self):
        executor = SandboxExecutor(SandboxConfig(sandbox_type=SandboxType.NONE))
        result = await executor.execute("echo hello")
        assert "hello" in result.stdout
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_execute_subprocess(self):
        executor = SandboxExecutor(SandboxConfig(sandbox_type=SandboxType.SUBPROCESS))
        result = await executor.execute("echo sandbox_test")
        assert "sandbox_test" in result.stdout

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        executor = SandboxExecutor(SandboxConfig(
            sandbox_type=SandboxType.NONE,
            timeout=1,
        ))
        result = await executor.execute("sleep 10")
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        executor = SandboxExecutor(SandboxConfig(sandbox_type=SandboxType.NONE))
        result = await executor.execute("exit 1")
        assert result.exit_code != 0


class TestSecurityConfig:
    def test_sandbox_fields_exist(self):
        from agent.core.config import SecurityConfig
        cfg = SecurityConfig()
        assert cfg.sandbox_type == "none"
        assert cfg.sandbox_docker_image == "python:3.12-slim"
        assert cfg.sandbox_memory_limit == "512m"
        assert cfg.sandbox_timeout == 30
        assert cfg.sandbox_network is False
