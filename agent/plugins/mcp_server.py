"""MCP Server — 将 xjd-agent 工具暴露为 MCP 服务器.

让 VS Code / Cursor / 其他 MCP 客户端可以直接调用 xjd-agent 的工具。

支持传输方式:
- stdio: 标准输入输出 (IDE 集成首选)
- sse: Server-Sent Events (HTTP)

用法:
    server = MCPServer(tool_registry=registry)
    await server.start_stdio()

CLI:
    xjd-agent serve-mcp          # stdio 模式
    xjd-agent serve-mcp --sse    # SSE 模式
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "xjd-agent"
SERVER_VERSION = "0.3.0"

class MCPServer:
    """MCP 服务器 — 暴露 ToolRegistry 中的工具."""

    def __init__(self, tool_registry: Any) -> None:
        self._registry = tool_registry
        self._initialized = False
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def _handle_message(self, message: dict) -> Optional[dict]:
        """处理 JSON-RPC 2.0 消息."""
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params", {})

        # 通知 (无 id) — 不需要响应
        if msg_id is None:
            return None

        if method == "initialize":
            return self._handle_initialize(msg_id, params)
        elif method == "tools/list":
            return self._handle_tools_list(msg_id)
        elif method == "tools/call":
            return await self._handle_tools_call(msg_id, params)
        elif method == "resources/list":
            return self._make_response(msg_id, {"resources": []})
        elif method == "resources/read":
            return self._make_error(msg_id, -32601, "资源不可用")
        else:
            return self._make_error(msg_id, -32601, f"未知方法: {method}")

    def _handle_initialize(self, msg_id: int, params: dict) -> dict:
        """处理 initialize 请求."""
        self._initialized = True
        return self._make_response(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": True},
            },
            "serverInfo": {
                "name": SERVER_NAME,
                "version": SERVER_VERSION,
            },
        })

    def _handle_tools_list(self, msg_id: int) -> dict:
        """返回所有已注册工具."""
        tools = []
        for t in self._registry.list_tools():
            if not t.enabled:
                continue
            tools.append({
                "name": t.name,
                "description": t.description,
                "inputSchema": {
                    "type": "object",
                    "properties": t.parameters.get("properties", {}),
                    "required": t.parameters.get("required", []),
                },
            })
        return self._make_response(msg_id, {"tools": tools})

    async def _handle_tools_call(self, msg_id: int, params: dict) -> dict:
        """执行工具调用."""
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        tool = self._registry.get(tool_name)
        if not tool:
            return self._make_error(msg_id, -32602, f"未知工具: {tool_name}")

        try:
            result = await self._registry.execute(tool_name, arguments)
            return self._make_response(msg_id, {
                "content": [{"type": "text", "text": str(result)}],
            })
        except Exception as e:
            return self._make_response(msg_id, {
                "content": [{"type": "text", "text": f"错误: {e}"}],
                "isError": True,
            })

    def _make_response(self, msg_id: int, result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _make_error(self, msg_id: int, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def _write_stdout(self, message: dict) -> None:
        line = json.dumps(message, ensure_ascii=False) + "\n"
        sys.stdout.write(line)
        sys.stdout.flush()

    async def start_stdio(self) -> None:
        """以 stdio 模式启动 MCP Server (IDE 集成)."""
        self._running = True
        logger.info("MCP Server (stdio) 已启动")

        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                message = json.loads(line.decode("utf-8").strip())
                response = await self._handle_message(message)
                if response:
                    self._write_stdout(response)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error("MCP stdio 错误: %s", e)
                break

        logger.info("MCP Server (stdio) 已停止")

    async def start_sse(self, host: str = "127.0.0.1", port: int = 8808) -> None:
        """以 SSE (Server-Sent Events) 模式启动 MCP Server."""
        from aiohttp import web

        self._running = True

        async def handle_sse(request: web.Request) -> web.StreamResponse:
            """SSE 端点 — 客户端通过此连接接收服务器消息."""
            resp = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Access-Control-Allow-Origin": "*",
                },
            )
            await resp.prepare(request)

            # 发送初始化就绪事件
            init_data = json.dumps({
                "jsonrpc": "2.0",
                "method": "server/ready",
                "params": {"protocolVersion": PROTOCOL_VERSION},
            }, ensure_ascii=False)
            await resp.write(f"data: {init_data}\n\n".encode())

            # 保持连接
            request.app["sse_clients"].append(resp)
            try:
                while self._running:
                    await asyncio.sleep(15)
                    await resp.write(b": keepalive\n\n")
            except (ConnectionResetError, asyncio.CancelledError):
                pass
            finally:
                request.app["sse_clients"].remove(resp)
            return resp

        async def handle_message(request: web.Request) -> web.Response:
            """POST 端点 — 客户端发送 JSON-RPC 消息."""
            try:
                message = await request.json()
                response = await self._handle_message(message)

                # 通过 SSE 广播响应
                if response:
                    event_data = json.dumps(response, ensure_ascii=False)
                    for client in request.app["sse_clients"]:
                        try:
                            await client.write(f"data: {event_data}\n\n".encode())
                        except Exception:
                            pass

                return web.json_response(response or {"status": "ok"})
            except Exception as e:
                return web.json_response(
                    {"jsonrpc": "2.0", "error": {"code": -32700, "message": str(e)}},
                    status=400,
                )

        app = web.Application()
        app["sse_clients"] = []
        app.router.add_get("/sse", handle_sse)
        app.router.add_post("/message", handle_message)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        logger.info("MCP Server (SSE) 已启动: http://%s:%d", host, port)

        try:
            while self._running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await runner.cleanup()
            logger.info("MCP Server (SSE) 已停止")
