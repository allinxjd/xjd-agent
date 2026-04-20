"""MCP (Model Context Protocol) 客户端 — 连接外部 MCP 工具服务器.

实现 MCP 协议的客户端部分:
- 通过 stdio / SSE / WebSocket 连接 MCP server
- 发现并注册远程工具
- 代理工具调用
- 支持 resources / prompts / sampling

参考: https://spec.modelcontextprotocol.io/specification/
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class MCPServerConfig:
    """MCP 服务器配置."""

    name: str = ""
    transport: str = "stdio"        # "stdio" | "sse" | "websocket"
    command: str = ""               # stdio mode: 启动命令
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""                   # SSE/WebSocket URL
    api_key: str = ""               # 鉴权
    enabled: bool = True

@dataclass
class MCPTool:
    """MCP 远程工具."""

    name: str = ""
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""

    def to_tool_definition(self) -> dict[str, Any]:
        return {
            "name": f"mcp_{self.server_name}_{self.name}",
            "description": f"[MCP:{self.server_name}] {self.description}",
            "parameters": self.input_schema,
        }

@dataclass
class MCPResource:
    """MCP 资源."""

    uri: str = ""
    name: str = ""
    description: str = ""
    mime_type: str = ""

class MCPConnection:
    """单个 MCP 服务器连接."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self._process: Optional[asyncio.subprocess.Process] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._tools: list[MCPTool] = []
        self._resources: list[MCPResource] = []
        self._connected = False
        self._read_task: Optional[asyncio.Task] = None
        # SSE transport state
        self._sse_client = None
        self._sse_base_url: str = ""
        self._sse_post_url: Optional[str] = None
        self._sse_response = None

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> bool:
        """连接到 MCP 服务器."""
        if self.config.transport == "stdio":
            return await self._connect_stdio()
        elif self.config.transport == "sse":
            return await self._connect_sse()
        else:
            logger.error("Unsupported transport: %s", self.config.transport)
            return False

    async def _connect_stdio(self) -> bool:
        """通过 stdio 连接."""
        try:
            env = {**os.environ, **self.config.env}
            cmd = self.config.command
            args = self.config.args

            self._process = await asyncio.create_subprocess_exec(
                cmd, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )

            if not self._process.stdout or not self._process.stdin:
                return False

            self._connected = True

            # 启动读取循环
            self._read_task = asyncio.create_task(self._read_loop_stdio())

            # 发送 initialize
            result = await self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                },
                "clientInfo": {
                    "name": "xjd-agent",
                    "version": "0.1.0",
                },
            })

            if result:
                # 发送 initialized 通知
                await self._send_notification("notifications/initialized", {})
                logger.info("Connected to MCP server: %s", self.config.name)
                return True

            return False

        except Exception as e:
            logger.error("Failed to connect to MCP server %s: %s", self.config.name, e)
            self._connected = False
            return False

    async def _connect_sse(self) -> bool:
        """通过 SSE 连接."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx required for SSE transport: pip install httpx")
            return False

        try:
            url = self.config.url
            if not url:
                logger.error("SSE transport requires url in config")
                return False

            headers: dict[str, str] = {"Accept": "text/event-stream"}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"

            self._sse_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=None, write=30.0, pool=10.0), headers=headers)
            self._sse_base_url = url.rstrip("/")

            # SSE endpoint 用于接收服务器推送，POST endpoint 用于发送请求
            # 先 GET /sse 获取 message endpoint
            self._sse_response = await self._sse_client.send(
                self._sse_client.build_request("GET", f"{self._sse_base_url}/sse"),
                stream=True,
            )

            # 读取第一个 SSE event 获取 message endpoint
            self._sse_post_url = None
            async for line in self._sse_response.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    endpoint = line[5:].strip()
                    if endpoint.startswith("/") or endpoint.startswith("http"):
                        if endpoint.startswith("/"):
                            self._sse_post_url = f"{self._sse_base_url}{endpoint}"
                        else:
                            self._sse_post_url = endpoint
                        break

            if not self._sse_post_url:
                logger.error("Failed to get SSE message endpoint")
                return False

            self._connected = True

            # 启动 SSE 读取循环
            self._read_task = asyncio.create_task(self._read_loop_sse())

            # 发送 initialize
            result = await self._send_request_sse("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {"roots": {"listChanged": True}},
                "clientInfo": {"name": "xjd-agent", "version": "0.1.0"},
            })

            if result:
                await self._send_notification_sse("notifications/initialized", {})
                logger.info("Connected to MCP server via SSE: %s", self.config.name)
                return True

            return False

        except Exception as e:
            logger.error("SSE connection failed for %s: %s", self.config.name, e)
            self._connected = False
            return False

    async def _read_loop_sse(self) -> None:
        """读取 SSE 事件流."""
        try:
            async for line in self._sse_response.aiter_lines():
                if not self._connected:
                    break
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    try:
                        message = json.loads(data_str)
                        await self._handle_message(message)
                    except json.JSONDecodeError:
                        continue
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("SSE read loop error: %s", e)
        finally:
            self._connected = False

    async def _send_request_sse(self, method: str, params: dict) -> Any:
        """通过 HTTP POST 发送 JSON-RPC 请求 (SSE mode)."""
        if not self._sse_post_url:
            return None

        self._request_id += 1
        req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        # Schedule auto-cleanup after 60s
        self._schedule_pending_timeout(req_id, 60.0)

        try:
            resp = await self._sse_client.post(
                self._sse_post_url,
                json=message,
                timeout=30.0,
            )
            resp.raise_for_status()
        except Exception as e:
            self._pending.pop(req_id, None)
            logger.error("SSE POST failed: %s", e)
            return None

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except asyncio.TimeoutError:
            logger.warning("SSE request timeout: %s", method)
            return None
        finally:
            self._pending.pop(req_id, None)

    async def _send_notification_sse(self, method: str, params: dict) -> None:
        """通过 HTTP POST 发送通知 (SSE mode)."""
        if not self._sse_post_url:
            return

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        try:
            await self._sse_client.post(self._sse_post_url, json=message, timeout=10.0)
        except Exception as e:
            logger.debug("SSE notification send failed: %s", e)

    async def _read_loop_stdio(self) -> None:
        """读取 stdio 输出."""
        if not self._process or not self._process.stdout:
            return

        try:
            while self._connected:
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode("utf-8").strip())
                    await self._handle_message(message)
                except json.JSONDecodeError:
                    continue

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("MCP read loop error: %s", e)
        finally:
            self._connected = False

    async def _handle_message(self, message: dict) -> None:
        """处理收到的消息."""
        if "id" in message and "result" in message:
            # 响应
            req_id = message["id"]
            if req_id in self._pending:
                self._pending[req_id].set_result(message.get("result"))
        elif "id" in message and "error" in message:
            # 错误响应
            req_id = message["id"]
            if req_id in self._pending:
                self._pending[req_id].set_result(None)
                logger.error("MCP error: %s", message["error"])
        elif "method" in message and "id" not in message:
            # 通知
            logger.debug("MCP notification: %s", message.get("method"))

    def _schedule_pending_timeout(self, req_id: int, timeout: float) -> None:
        """Schedule auto-cancellation of a pending future after timeout."""
        def _timeout_cb():
            future = self._pending.pop(req_id, None)
            if future and not future.done():
                future.cancel()
                logger.debug("MCP pending request %d timed out after %.0fs", req_id, timeout)

        loop = asyncio.get_event_loop()
        loop.call_later(timeout, _timeout_cb)

    async def _send_request(self, method: str, params: dict) -> Any:
        """发送 JSON-RPC 请求."""
        if not self._process or not self._process.stdin:
            return None

        self._request_id += 1
        req_id = self._request_id

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        # Schedule auto-cleanup after 60s
        self._schedule_pending_timeout(req_id, 60.0)

        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            return result
        except asyncio.TimeoutError:
            logger.warning("MCP request timeout: %s", method)
            return None
        finally:
            self._pending.pop(req_id, None)

    async def _send_notification(self, method: str, params: dict) -> None:
        """发送通知 (无 id)."""
        if not self._process or not self._process.stdin:
            return

        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }

        data = json.dumps(message) + "\n"
        self._process.stdin.write(data.encode("utf-8"))
        await self._process.stdin.drain()

    async def list_tools(self) -> list[MCPTool]:
        """获取可用工具列表."""
        if self.config.transport == "sse":
            result = await self._send_request_sse("tools/list", {})
        else:
            result = await self._send_request("tools/list", {})
        if not result or "tools" not in result:
            return []

        self._tools = []
        for t in result["tools"]:
            tool = MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.config.name,
            )
            self._tools.append(tool)

        return self._tools

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用工具."""
        if self.config.transport == "sse":
            result = await self._send_request_sse("tools/call", {
                "name": name, "arguments": arguments,
            })
        else:
            result = await self._send_request("tools/call", {
                "name": name, "arguments": arguments,
            })

        if not result:
            return "Error: MCP tool call failed"

        # 解析结果
        content_parts = result.get("content", [])
        texts = []
        for part in content_parts:
            if part.get("type") == "text":
                texts.append(part.get("text", ""))
            elif part.get("type") == "image":
                texts.append(f"[Image: {part.get('mimeType', 'image')}]")

        return "\n".join(texts) if texts else str(result)

    async def list_resources(self) -> list[MCPResource]:
        """获取可用资源."""
        if self.config.transport == "sse":
            result = await self._send_request_sse("resources/list", {})
        else:
            result = await self._send_request("resources/list", {})
        if not result or "resources" not in result:
            return []

        self._resources = []
        for r in result["resources"]:
            res = MCPResource(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", ""),
            )
            self._resources.append(res)

        return self._resources

    async def read_resource(self, uri: str) -> str:
        """读取资源."""
        if self.config.transport == "sse":
            result = await self._send_request_sse("resources/read", {"uri": uri})
        else:
            result = await self._send_request("resources/read", {"uri": uri})
        if not result or "contents" not in result:
            return ""

        texts = []
        for content in result["contents"]:
            if "text" in content:
                texts.append(content["text"])

        return "\n".join(texts)

    async def disconnect(self) -> None:
        """断开连接."""
        await self.close()

    async def close(self) -> None:
        """关闭连接并清理所有资源."""
        self._connected = False

        # Cancel all pending futures
        for req_id, future in list(self._pending.items()):
            if not future.done():
                future.cancel()
        self._pending.clear()

        if self._read_task:
            self._read_task.cancel()
            self._read_task = None

        if self._process:
            try:
                self._process.kill()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                pass
            self._process = None

        # Close writer (stdio)
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None

        # 清理 SSE 资源
        if self._sse_response:
            try:
                await self._sse_response.aclose()
            except Exception:
                pass
            self._sse_response = None
        if self._sse_client:
            try:
                await self._sse_client.aclose()
            except Exception:
                pass
            self._sse_client = None
        self._sse_post_url = None

        logger.info("Disconnected from MCP server: %s", self.config.name)

    async def __aenter__(self) -> "MCPConnection":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

class MCPClient:
    """MCP 客户端管理器 — 管理多个 MCP 服务器连接.

    用法:
        mcp = MCPClient()
        mcp.add_server(MCPServerConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        ))
        await mcp.connect_all()

        tools = mcp.get_all_tools()      # 获取所有远程工具
        result = await mcp.call_tool("mcp_filesystem_read_file", {"path": "/tmp/test"})
    """

    def __init__(self) -> None:
        self._connections: dict[str, MCPConnection] = {}
        self._tool_map: dict[str, tuple[MCPConnection, str]] = {}  # qualified_name → (conn, orig_name)

    def add_server(self, config: MCPServerConfig) -> None:
        """添加 MCP 服务器."""
        if config.name in self._connections:
            logger.warning("MCP server %s already exists, replacing", config.name)
        self._connections[config.name] = MCPConnection(config)

    def remove_server(self, name: str) -> None:
        """移除 MCP 服务器."""
        self._connections.pop(name, None)
        # 清理相关工具
        to_remove = [k for k, (conn, _) in self._tool_map.items() if conn.config.name == name]
        for k in to_remove:
            del self._tool_map[k]

    async def connect_all(self) -> dict[str, bool]:
        """连接所有已配置的服务器."""
        results = {}
        for name, conn in self._connections.items():
            if not conn.config.enabled:
                results[name] = False
                continue
            ok = await conn.connect()
            results[name] = ok
            if ok:
                # 发现工具
                tools = await conn.list_tools()
                for tool in tools:
                    qualified = f"mcp_{name}_{tool.name}"
                    self._tool_map[qualified] = (conn, tool.name)
                logger.info("MCP %s: %d tools discovered", name, len(tools))

        return results

    async def disconnect_all(self) -> None:
        """断开所有连接."""
        for conn in self._connections.values():
            await conn.disconnect()
        self._tool_map.clear()

    def get_all_tools(self) -> list[dict[str, Any]]:
        """获取所有 MCP 工具定义 (转换为 Agent 工具格式)."""
        tools = []
        for conn in self._connections.values():
            if not conn.connected:
                continue
            for tool in conn._tools:
                definition = tool.to_tool_definition()
                tools.append(definition)
        return tools

    async def call_tool(self, qualified_name: str, arguments: dict) -> str:
        """调用 MCP 工具.

        Args:
            qualified_name: 限定名 (如 "mcp_filesystem_read_file")
            arguments: 参数
        """
        entry = self._tool_map.get(qualified_name)
        if not entry:
            return f"Error: Unknown MCP tool '{qualified_name}'"

        conn, orig_name = entry
        if not conn.connected:
            return f"Error: MCP server '{conn.config.name}' not connected"

        return await conn.call_tool(orig_name, arguments)

    def create_tool_handler(self, qualified_name: str):
        """为 MCP 工具创建 handler 函数 (用于注册到 AgentEngine)."""
        async def handler(**kwargs):
            return await self.call_tool(qualified_name, kwargs)
        return handler

    @property
    def server_count(self) -> int:
        return len(self._connections)

    @property
    def connected_count(self) -> int:
        return sum(1 for c in self._connections.values() if c.connected)

    @property
    def tool_count(self) -> int:
        return len(self._tool_map)
