"""plugins 模块 — 可热插拔插件系统 + MCP 协议支持.

包含:
  - PluginManager: 插件发现、加载、生命周期管理
  - BasePlugin: 插件基类 ABC
  - MCPClient: Model Context Protocol 客户端
"""

from agent.plugins.manager import BasePlugin, PluginManager, PluginState, PluginInfo
from agent.plugins.mcp_client import MCPClient, MCPServerConfig

__all__ = [
    "BasePlugin",
    "PluginManager",
    "PluginState",
    "PluginInfo",
    "MCPClient",
    "MCPServerConfig",
]
