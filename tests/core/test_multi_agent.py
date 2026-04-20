"""测试 — 多 Agent 协作."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.multi_agent import (
    AgentRole,
    BUILTIN_ROLES,
    MultiAgentManager,
    SubAgentResult,
)


class TestAgentRole:
    def test_builtin_roles(self):
        """内置角色应有 4 个."""
        assert len(BUILTIN_ROLES) == 4
        names = [r.name for r in BUILTIN_ROLES]
        assert "coder" in names
        assert "researcher" in names
        assert "analyst" in names
        assert "executor" in names

    def test_role_has_keywords(self):
        for role in BUILTIN_ROLES:
            assert len(role.keywords) > 0
            assert role.system_prompt
            assert role.description


class TestMultiAgentManager:
    def _make_manager(self):
        router = MagicMock()
        registry = MagicMock()
        registry.list_tools.return_value = []
        return MultiAgentManager(router=router, tool_registry=registry)

    def test_init_registers_builtin_roles(self):
        mgr = self._make_manager()
        roles = mgr.list_roles()
        assert len(roles) == 4

    def test_register_custom_role(self):
        mgr = self._make_manager()
        mgr.register_role(AgentRole(
            name="custom",
            description="Custom role",
            system_prompt="You are custom.",
        ))
        assert mgr.get_role("custom") is not None
        assert len(mgr.list_roles()) == 5

    def test_select_role_coder(self):
        mgr = self._make_manager()
        role = mgr._select_role("帮我写一个排序函数的代码")
        assert role.name == "coder"

    def test_select_role_researcher(self):
        mgr = self._make_manager()
        role = mgr._select_role("搜索一下最新的 AI 论文")
        assert role.name == "researcher"

    def test_select_role_executor(self):
        mgr = self._make_manager()
        role = mgr._select_role("部署服务到服务器上")
        assert role.name == "executor"

    def test_select_role_analyst(self):
        mgr = self._make_manager()
        role = mgr._select_role("分析这份数据的统计结果")
        assert role.name == "analyst"

    @pytest.mark.asyncio
    async def test_delegate_unknown_role(self):
        mgr = self._make_manager()
        result = await mgr.delegate(task="test", role="nonexistent")
        assert "未知角色" in result

    @pytest.mark.asyncio
    async def test_spawn_unknown_role(self):
        mgr = self._make_manager()
        result = await mgr.spawn_agent("nonexistent", "test")
        assert result.success is False
        assert "未知角色" in result.error
