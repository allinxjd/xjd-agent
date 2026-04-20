"""测试 — Computer Use 桌面控制工具."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.tools.registry import ToolRegistry


class TestComputerUseRegistration:
    """测试工具注册."""

    def test_register_with_pyautogui(self):
        """pyautogui 可用时应注册 4 个工具."""
        from agent.tools.computer_use import register_computer_use_tools

        reg = ToolRegistry()
        register_computer_use_tools(reg)

        tools = reg.list_tools()
        names = [t.name for t in tools]

        assert "mouse_control" in names
        assert "keyboard_control" in names
        assert "window_manager" in names
        assert "screen_ocr" in names
        assert len(tools) == 4

    def test_all_require_approval(self):
        """所有 Computer Use 工具都需要审批."""
        from agent.tools.computer_use import register_computer_use_tools

        reg = ToolRegistry()
        register_computer_use_tools(reg)

        for tool in reg.list_tools():
            assert tool.requires_approval is True, f"{tool.name} should require approval"

    def test_category_is_computer_use(self):
        """所有工具分类为 computer-use."""
        from agent.tools.computer_use import register_computer_use_tools

        reg = ToolRegistry()
        register_computer_use_tools(reg)

        for tool in reg.list_tools():
            assert tool.category == "computer-use"


class TestMouseControl:
    @pytest.mark.asyncio
    async def test_position(self):
        """获取鼠标位置."""
        from agent.tools.computer_use import _mouse_control

        mock_pos = MagicMock()
        mock_pos.x = 100
        mock_pos.y = 200

        with patch("agent.tools.computer_use._check_pyautogui") as mock_check:
            mock_pyautogui = MagicMock()
            mock_pyautogui.position.return_value = mock_pos
            mock_check.return_value = mock_pyautogui

            result = await _mouse_control(action="position")
            assert "100" in result
            assert "200" in result

    @pytest.mark.asyncio
    async def test_click(self):
        from agent.tools.computer_use import _mouse_control

        with patch("agent.tools.computer_use._check_pyautogui") as mock_check:
            mock_pyautogui = MagicMock()
            mock_check.return_value = mock_pyautogui

            result = await _mouse_control(action="click", x=50, y=50)
            assert "点击" in result
            mock_pyautogui.click.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        from agent.tools.computer_use import _mouse_control

        with patch("agent.tools.computer_use._check_pyautogui") as mock_check:
            mock_check.return_value = MagicMock()
            result = await _mouse_control(action="invalid")
            assert "未知操作" in result


class TestKeyboardControl:
    @pytest.mark.asyncio
    async def test_press(self):
        from agent.tools.computer_use import _keyboard_control

        with patch("agent.tools.computer_use._check_pyautogui") as mock_check:
            mock_pyautogui = MagicMock()
            mock_check.return_value = mock_pyautogui

            result = await _keyboard_control(action="press", key="enter")
            assert "按键" in result
            mock_pyautogui.press.assert_called_once_with("enter")

    @pytest.mark.asyncio
    async def test_hotkey(self):
        from agent.tools.computer_use import _keyboard_control

        with patch("agent.tools.computer_use._check_pyautogui") as mock_check:
            mock_pyautogui = MagicMock()
            mock_check.return_value = mock_pyautogui

            result = await _keyboard_control(action="hotkey", hotkey="ctrl+c")
            assert "组合键" in result
            mock_pyautogui.hotkey.assert_called_once_with("ctrl", "c")

    @pytest.mark.asyncio
    async def test_press_missing_key(self):
        from agent.tools.computer_use import _keyboard_control

        with patch("agent.tools.computer_use._check_pyautogui") as mock_check:
            mock_check.return_value = MagicMock()
            result = await _keyboard_control(action="press")
            assert "错误" in result


class TestWindowManager:
    @pytest.mark.asyncio
    async def test_unknown_action(self):
        from agent.tools.computer_use import _window_manager

        result = await _window_manager(action="invalid")
        assert "未知操作" in result

    @pytest.mark.asyncio
    async def test_activate_missing_title(self):
        from agent.tools.computer_use import _window_manager

        result = await _window_manager(action="activate")
        assert "错误" in result


class TestDockerFiles:
    """验证 Docker 配置文件存在且格式正确."""

    def test_dockerfile_exists(self):
        from pathlib import Path
        assert (Path(__file__).parents[2] / "docker" / "Dockerfile").exists()

    def test_compose_exists(self):
        from pathlib import Path
        assert (Path(__file__).parents[2] / "docker" / "docker-compose.yml").exists()

    def test_dockerignore_exists(self):
        from pathlib import Path
        assert (Path(__file__).parents[2] / ".dockerignore").exists()

    def test_env_example_exists(self):
        from pathlib import Path
        assert (Path(__file__).parents[2] / "docker" / ".env.example").exists()

    def test_compose_has_profiles(self):
        from pathlib import Path
        import yaml
        compose_path = Path(__file__).parents[2] / "docker" / "docker-compose.yml"
        with open(compose_path) as f:
            data = yaml.safe_load(f)
        services = data.get("services", {})
        assert "agent" in services
        assert "agent-web" in services
        assert "agent-chat" in services
        assert "redis" in services
