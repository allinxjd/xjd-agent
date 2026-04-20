# 插件开发指南

## 创建插件

每个插件是一个继承 `BasePlugin` 的 Python 类：

```python
from agent.plugins.manager import BasePlugin

class MyPlugin(BasePlugin):
    """插件描述."""

    async def on_install(self) -> None:
        """安装时调用 (一次性初始化)."""
        pass

    async def on_enable(self) -> None:
        """启用时调用."""
        pass

    async def on_disable(self) -> None:
        """禁用时调用."""
        pass

    async def on_uninstall(self) -> None:
        """卸载时调用."""
        pass

    def get_tools(self) -> list[dict]:
        """注册工具."""
        return [
            {
                "name": "my_tool",
                "description": "工具描述",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "查询内容"},
                    },
                    "required": ["query"],
                },
                "handler": self._my_handler,
                "requires_approval": False,
            }
        ]

    async def _my_handler(self, query: str) -> str:
        """工具实现."""
        return f"处理结果: {query}"
```

## 插件配置

在 `config.yaml` 或运行时配置：

```yaml
plugins:
  weather:
    enabled: true
    config:
      api_key: "your-openweathermap-key"
      default_city: "上海"
  github:
    enabled: true
    config:
      token: "ghp_xxx"
```

插件内通过 `self.config` 访问配置。

## 内置示例插件

### 1. WeatherPlugin — 天气查询

工具: `get_weather`, `get_forecast`

```python
from agent.plugins.examples.weather_plugin import WeatherPlugin
```

### 2. KnowledgeBasePlugin — 知识库 RAG

工具: `kb_search`, `kb_load`, `kb_stats`

```python
from agent.plugins.examples.knowledge_base_plugin import KnowledgeBasePlugin
```

### 3. GitHubPlugin — GitHub 集成

工具: `github_search_repos`, `github_list_issues`, `github_create_issue`, `github_get_file`

```python
from agent.plugins.examples.github_plugin import GitHubPlugin
```

## MCP 协议

### MCP Client — 连接外部工具服务

支持通过 MCP (Model Context Protocol) 连接外部工具服务：

```python
from agent.plugins.mcp_client import MCPClient, MCPServerConfig

client = MCPClient()
await client.connect_server(MCPServerConfig(
    name="my-mcp-server",
    command="npx",
    args=["-y", "@my/mcp-server"],
))

# 自动注册为工具: "my-mcp-server::tool_name"
tools = await client.list_all_tools()
result = await client.call_tool("my-mcp-server", "tool_name", {"arg": "value"})
```

## 插件生命周期

```
scan_plugins() → 发现插件
    ↓
install(name) → on_install() → 状态: INSTALLED
    ↓
enable(name) → on_enable() + 注册工具 → 状态: ENABLED
    ↓
disable(name) → on_disable() + 注销工具 → 状态: DISABLED
    ↓
uninstall(name) → on_uninstall() → 移除
```

### MCP Server — 供 IDE 调用

xjd-agent 可以作为 MCP Server 运行，让 VS Code / Cursor 等 IDE 直接调用 Agent 的工具：

```bash
# 启动 MCP Server (stdio 模式)
xjd-agent serve-mcp
```

在 VS Code 的 `.vscode/mcp.json` 中配置：

```json
{
  "servers": {
    "xjd-agent": {
      "command": "xjd-agent",
      "args": ["serve-mcp"]
    }
  }
}
```

MCP Server 会自动暴露 ToolRegistry 中注册的所有工具，IDE 可以通过 MCP 协议调用。

协议: JSON-RPC 2.0 over stdio，版本 `2024-11-05`。
