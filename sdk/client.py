"""XJD Agent Python SDK — 提供编程式接口.

用法:
    from sdk.client import XJDClient

    # 连接到远程 Agent
    client = XJDClient(base_url="http://localhost:8080")

    # 认证
    token = await client.auth.login("admin", "your-password")

    # 或直接内嵌使用 (无需认证)
    client = XJDClient.embedded()

    # 聊天
    response = await client.chat("你好")
    print(response.content)

    # 流式聊天
    async for chunk in client.chat_stream("写一首诗"):
        print(chunk, end="")

    # 工具调用
    result = await client.execute_tool("web_search", {"query": "Python news"})

    # 记忆
    await client.memory.add("用户喜欢 Python")
    results = await client.memory.search("编程偏好")

    # 管理
    users = await client.admin.list_users()
    log = await client.admin.audit_log(limit=20)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

logger = logging.getLogger(__name__)


@dataclass
class ChatResponse:
    """聊天响应."""

    content: str = ""
    tool_calls: int = 0
    tokens: int = 0
    duration_ms: float = 0.0
    model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """工具执行结果."""

    tool: str = ""
    success: bool = True
    result: str = ""
    error: str = ""
    duration_ms: float = 0.0


class MemoryClient:
    """记忆子客户端."""

    def __init__(self, client: XJDClient) -> None:
        self._client = client

    async def add(self, content: str, memory_type: str = "fact", metadata: dict | None = None) -> str:
        """添加记忆."""
        data = await self._client._post("/api/memory", {
            "content": content,
            "type": memory_type,
            "metadata": metadata or {},
        })
        return data.get("memory_id", "")

    async def search(self, query: str, top_k: int = 5) -> list[dict]:
        """搜索记忆."""
        data = await self._client._post("/api/memory/search", {
            "query": query,
            "top_k": top_k,
        })
        return data.get("results", [])

    async def list(self, memory_type: str | None = None, limit: int = 50) -> list[dict]:
        """列出记忆."""
        params = {"limit": limit}
        if memory_type:
            params["type"] = memory_type
        data = await self._client._get("/api/memory", params)
        return data.get("memories", [])

    async def delete(self, memory_id: str) -> bool:
        """删除记忆."""
        data = await self._client._delete(f"/api/memory/{memory_id}")
        return data.get("success", False)


class PluginClient:
    """插件子客户端."""

    def __init__(self, client: XJDClient) -> None:
        self._client = client

    async def list(self) -> list[dict]:
        data = await self._client._get("/api/plugins")
        return data.get("plugins", [])

    async def enable(self, name: str) -> bool:
        data = await self._client._post(f"/api/plugins/{name}/enable", {})
        return data.get("success", False)

    async def disable(self, name: str) -> bool:
        data = await self._client._post(f"/api/plugins/{name}/disable", {})
        return data.get("success", False)


class AuthClient:
    """认证子客户端."""

    def __init__(self, client: XJDClient) -> None:
        self._client = client

    async def login(self, username: str, password: str) -> str:
        """登录并返回 JWT token. 同时自动设置到客户端."""
        data = await self._client._post("/api/auth/login", {
            "username": username,
            "password": password,
        })
        token = data.get("token", "")
        if token:
            self._client._api_key = token
        return token

    async def register(self, username: str, password: str, role: str = "user") -> dict:
        """注册新用户 (需要 admin 权限)."""
        return await self._client._post("/api/auth/register", {
            "username": username,
            "password": password,
            "role": role,
        })


class AdminClient:
    """管理子客户端."""

    def __init__(self, client: XJDClient) -> None:
        self._client = client

    async def stats(self) -> dict:
        return await self._client._get("/api/admin/stats")

    async def models(self) -> dict:
        return await self._client._get("/api/admin/models")

    async def tools(self) -> dict:
        return await self._client._get("/api/admin/tools")

    async def health(self) -> dict:
        return await self._client._get("/health")

    async def audit_log(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """获取审计日志."""
        data = await self._client._get("/api/admin/audit", {
            "limit": limit, "offset": offset,
        })
        return data.get("entries", [])

    async def list_users(self) -> list[dict]:
        """列出所有用户."""
        data = await self._client._get("/api/admin/users")
        return data.get("users", [])

    async def create_user(self, username: str, password: str, role: str = "user") -> dict:
        """创建用户."""
        return await self._client._post("/api/admin/users", {
            "username": username,
            "password": password,
            "role": role,
        })

    async def get_system_prompt(self) -> str:
        """获取系统提示词."""
        data = await self._client._get("/api/admin/system-prompt")
        return data.get("prompt", "")

    async def set_system_prompt(self, prompt: str) -> bool:
        """设置系统提示词."""
        data = await self._client._post("/api/admin/system-prompt", {
            "prompt": prompt,
        })
        return data.get("success", False)


class XJDClient:
    """XJD Agent SDK 客户端.

    支持两种模式:
    1. HTTP 模式 — 连接到远程 Agent Server
    2. 内嵌模式 — 直接调用 Agent Engine (无网络开销)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        api_key: str = "",
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._engine = None  # 内嵌模式

        # 子客户端
        self.auth = AuthClient(self)
        self.memory = MemoryClient(self)
        self.plugins = PluginClient(self)
        self.admin = AdminClient(self)

    @classmethod
    def embedded(cls, config: dict | None = None) -> XJDClient:
        """创建内嵌模式客户端 (直接调用引擎)."""
        client = cls()
        # 延迟初始化引擎
        client._engine = None  # Will be initialized on first call
        return client

    async def _ensure_engine(self) -> None:
        """确保内嵌引擎已初始化."""
        if self._engine is not None:
            return

        try:
            from agent.core.config import Config
            from agent.core.engine import AgentEngine
            from agent.core.model_router import ModelRouter
            from agent.providers.openai_provider import OpenAIProvider
            from agent.providers.base import ProviderType
            from agent.tools.builtin import register_builtin_tools
            from agent.tools.registry import ToolRegistry

            config = Config.load()
            config.apply_env_overrides()

            from agent.core.model_router import build_credential_manager_from_config
            cred_mgr = build_credential_manager_from_config(config)
            router = ModelRouter(credential_manager=cred_mgr)
            primary = config.model.primary
            if primary.provider and primary.api_key:
                provider = OpenAIProvider(
                    provider_type=ProviderType(primary.provider),
                    api_key=primary.api_key,
                    base_url=primary.base_url or None,
                )
                router.register_provider(provider)
                router.set_primary(primary.provider, primary.model)

            self._engine = AgentEngine(router=router)

            tool_registry = ToolRegistry()
            register_builtin_tools(tool_registry)
            for tool in tool_registry.list_tools():
                self._engine.register_tool(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    handler=tool.handler,
                    requires_approval=tool.requires_approval,
                )
        except Exception as e:
            logger.error("Failed to initialize embedded engine: %s", e)
            raise RuntimeError(f"Embedded engine initialization failed: {e}") from e

    async def chat(
        self,
        message: str,
        stream: bool = False,
        thinking: str | None = None,
    ) -> ChatResponse:
        """发送聊天消息.

        Args:
            message: 用户消息
            stream: 是否流式 (如果 True，使用 chat_stream 代替)
            thinking: 思考级别

        Returns:
            ChatResponse
        """
        if self._engine is not None or self._base_url == "http://localhost:8080":
            # 内嵌模式
            await self._ensure_engine()
            result = await self._engine.run_turn(message, thinking=thinking)
            return ChatResponse(
                content=result.content,
                tool_calls=result.tool_calls_made,
                tokens=result.total_usage.total_tokens,
                duration_ms=result.duration_ms,
            )
        else:
            # HTTP 模式
            data = await self._post("/api/chat", {
                "message": message,
                "thinking": thinking,
            })
            return ChatResponse(
                content=data.get("content", ""),
                tool_calls=data.get("tool_calls", 0),
                tokens=data.get("tokens", 0),
                duration_ms=data.get("duration_ms", 0),
            )

    async def chat_stream(
        self,
        message: str,
        thinking: str | None = None,
    ) -> AsyncIterator[str]:
        """流式聊天."""
        if self._engine is not None or self._base_url == "http://localhost:8080":
            await self._ensure_engine()
            chunks: list[str] = []

            def on_stream(text: str):
                chunks.append(text)

            await self._engine.run_turn(
                message,
                on_stream=on_stream,
                thinking=thinking,
            )

            for chunk in chunks:
                yield chunk
        else:
            # WebSocket 流式
            try:
                import websockets

                ws_url = self._base_url.replace("http", "ws") + "/ws"
                async with websockets.connect(ws_url, open_timeout=15, close_timeout=10) as ws:
                    await ws.send(json.dumps({
                        "type": "chat",
                        "message": message,
                    }))

                    async for raw in ws:
                        data = json.loads(raw)
                        if data["type"] == "stream":
                            yield data.get("content", "")
                        elif data["type"] == "complete":
                            break
                        elif data["type"] == "error":
                            raise RuntimeError(data.get("message", "Unknown error"))

            except ImportError:
                raise ImportError("websockets 未安装。请运行: pip install websockets")

    async def execute_tool(self, tool_name: str, args: dict | None = None) -> ToolResult:
        """直接执行工具."""
        import time
        start = time.time()

        if self._engine is not None or self._base_url == "http://localhost:8080":
            await self._ensure_engine()
            try:
                result = await self._engine._execute_tool(tool_name, json.dumps(args or {}))
                return ToolResult(
                    tool=tool_name,
                    success=True,
                    result=result,
                    duration_ms=(time.time() - start) * 1000,
                )
            except Exception as e:
                return ToolResult(
                    tool=tool_name,
                    success=False,
                    error=str(e),
                    duration_ms=(time.time() - start) * 1000,
                )
        else:
            data = await self._post("/api/tool", {
                "tool": tool_name,
                "args": args or {},
            })
            return ToolResult(
                tool=tool_name,
                success=data.get("success", False),
                result=data.get("result", ""),
                error=data.get("error", ""),
            )

    async def reset(self) -> None:
        """重置对话。"""
        if self._engine:
            self._engine.reset()
        else:
            await self._post("/api/reset", {})

    # ─── HTTP 传输层 ───

    async def _get(self, path: str, params: dict | None = None) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}") from e
        except ValueError:
            raise RuntimeError("Invalid JSON response from server")

    async def _post(self, path: str, data: dict) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}{path}",
                    json=data,
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}") from e
        except ValueError:
            raise RuntimeError("Invalid JSON response from server")

    async def _delete(self, path: str) -> dict:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.delete(
                    f"{self._base_url}{path}",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(f"HTTP {e.response.status_code}: {e.response.text[:200]}") from e
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}") from e
        except ValueError:
            raise RuntimeError("Invalid JSON response from server")

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h
