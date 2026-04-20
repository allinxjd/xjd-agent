"""测试 — MCP Server."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent.plugins.mcp_server import MCPServer, PROTOCOL_VERSION, SERVER_NAME


def _make_registry(*tools):
    """创建 mock ToolRegistry."""
    registry = MagicMock()
    mock_tools = []
    for name, desc, params in tools:
        t = MagicMock()
        t.name = name
        t.description = desc
        t.parameters = params
        t.enabled = True
        mock_tools.append(t)
    registry.list_tools.return_value = mock_tools
    return registry


class TestMCPServerInit:
    def test_create(self):
        registry = MagicMock()
        server = MCPServer(tool_registry=registry)
        assert server._initialized is False
        assert server._running is False


class TestHandleInitialize:
    @pytest.mark.asyncio
    async def test_initialize(self):
        server = MCPServer(tool_registry=MagicMock())
        msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "clientInfo": {"name": "test", "version": "1.0"},
        }}
        resp = await server._handle_message(msg)
        assert resp["id"] == 1
        assert resp["result"]["protocolVersion"] == PROTOCOL_VERSION
        assert resp["result"]["serverInfo"]["name"] == SERVER_NAME
        assert server._initialized is True


class TestHandleToolsList:
    @pytest.mark.asyncio
    async def test_list_tools(self):
        registry = _make_registry(
            ("run_terminal", "执行命令", {"properties": {"command": {"type": "string"}}, "required": ["command"]}),
            ("read_file", "读取文件", {"properties": {"path": {"type": "string"}}, "required": ["path"]}),
        )
        server = MCPServer(tool_registry=registry)
        msg = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = await server._handle_message(msg)
        tools = resp["result"]["tools"]
        assert len(tools) == 2
        assert tools[0]["name"] == "run_terminal"
        assert tools[1]["name"] == "read_file"

    @pytest.mark.asyncio
    async def test_disabled_tools_excluded(self):
        registry = MagicMock()
        t = MagicMock()
        t.name = "disabled_tool"
        t.enabled = False
        registry.list_tools.return_value = [t]
        server = MCPServer(tool_registry=registry)
        msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}}
        resp = await server._handle_message(msg)
        assert len(resp["result"]["tools"]) == 0


class TestHandleToolsCall:
    @pytest.mark.asyncio
    async def test_call_tool(self):
        registry = MagicMock()
        registry.get.return_value = MagicMock()
        registry.execute = AsyncMock(return_value="hello world")
        server = MCPServer(tool_registry=registry)
        msg = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "run_terminal", "arguments": {"command": "echo hello"},
        }}
        resp = await server._handle_message(msg)
        assert resp["result"]["content"][0]["text"] == "hello world"

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self):
        registry = MagicMock()
        registry.get.return_value = None
        server = MCPServer(tool_registry=registry)
        msg = {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "nonexistent", "arguments": {},
        }}
        resp = await server._handle_message(msg)
        assert "error" in resp
        assert resp["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_call_tool_error(self):
        registry = MagicMock()
        registry.get.return_value = MagicMock()
        registry.execute = AsyncMock(side_effect=RuntimeError("boom"))
        server = MCPServer(tool_registry=registry)
        msg = {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": "run_terminal", "arguments": {},
        }}
        resp = await server._handle_message(msg)
        assert resp["result"]["isError"] is True


class TestHandleUnknown:
    @pytest.mark.asyncio
    async def test_unknown_method(self):
        server = MCPServer(tool_registry=MagicMock())
        msg = {"jsonrpc": "2.0", "id": 7, "method": "unknown/method", "params": {}}
        resp = await server._handle_message(msg)
        assert "error" in resp

    @pytest.mark.asyncio
    async def test_notification_no_response(self):
        server = MCPServer(tool_registry=MagicMock())
        msg = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        resp = await server._handle_message(msg)
        assert resp is None

    @pytest.mark.asyncio
    async def test_resources_list(self):
        server = MCPServer(tool_registry=MagicMock())
        msg = {"jsonrpc": "2.0", "id": 8, "method": "resources/list", "params": {}}
        resp = await server._handle_message(msg)
        assert resp["result"]["resources"] == []
