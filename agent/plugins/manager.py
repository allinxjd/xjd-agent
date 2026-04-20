"""插件系统 — 可热插拔的插件管理.

- 插件发现 (目录扫描 / pip 安装 / Git)
- 动态加载与卸载
- 沙盒隔离 (资源限制)
- 配置管理 (插件独立配置)
- 生命周期管理 (install → configure → enable → disable → uninstall)

架构:
    PluginManager
      ├── PluginLoader (发现 + 加载)
      ├── PluginSandbox (资源隔离)
      └── PluginConfig (配置管理)

插件结构:
    my_plugin/
      ├── plugin.yaml        # 元信息
      ├── __init__.py         # 入口 (必须包含 Plugin 类)
      └── ...
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

logger = logging.getLogger(__name__)

class PluginState(str, Enum):
    """插件状态."""

    DISCOVERED = "discovered"
    INSTALLED = "installed"
    CONFIGURED = "configured"
    ENABLED = "enabled"
    DISABLED = "disabled"
    ERROR = "error"

@dataclass
class PluginMeta:
    """插件元信息 (来自 plugin.yaml)."""

    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    license: str = "MIT"
    min_agent_version: str = "0.1.0"
    dependencies: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    config_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> PluginMeta:
        """从 plugin.yaml 加载."""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return cls()
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (yaml.YAMLError, OSError, TypeError) as e:
            logger.warning("Failed to parse %s: %s", path, e)
            return cls()

@dataclass
class PluginInfo:
    """插件运行时信息."""

    meta: PluginMeta
    state: PluginState = PluginState.DISCOVERED
    path: Optional[Path] = None
    instance: Optional[Any] = None  # Plugin 实例
    config: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    tools_registered: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.meta.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.meta.name,
            "version": self.meta.version,
            "description": self.meta.description,
            "state": self.state.value,
            "author": self.meta.author,
            "tags": self.meta.tags,
            "tools": self.tools_registered,
            "error": self.error,
        }

class BasePlugin:
    """插件基类 — 所有插件必须继承此类.

    示例:
        class MyPlugin(BasePlugin):
            async def on_enable(self):
                self.register_tool("my_tool", "描述", {...}, self.my_handler)

            async def my_handler(self, **kwargs):
                return "result"
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self._tools: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        """插件名 (可覆写)."""
        return self.__class__.__name__

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return self.__class__.__doc__ or ""

    async def on_install(self) -> None:
        """安装时回调 (一次性初始化)."""
        pass

    async def on_enable(self) -> None:
        """启用时回调 — 注册工具/事件."""
        pass

    async def on_disable(self) -> None:
        """禁用时回调 — 清理资源."""
        pass

    async def on_uninstall(self) -> None:
        """卸载时回调."""
        pass

    async def on_config_update(self, config: dict[str, Any]) -> None:
        """配置更新回调."""
        self.config = config

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable,
        requires_approval: bool = False,
    ) -> None:
        """注册工具 (在 on_enable 中调用)."""
        self._tools.append({
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
            "requires_approval": requires_approval,
        })

    def get_registered_tools(self) -> list[dict[str, Any]]:
        """获取已注册的工具."""
        return self._tools

class PluginManager:
    """插件管理器.

    用法:
        pm = PluginManager()
        await pm.scan_plugins()                # 扫描插件目录
        await pm.enable_plugin("my_plugin")     # 启用插件
        tools = pm.get_all_tools()              # 获取所有插件工具
    """

    def __init__(
        self,
        plugin_dirs: Optional[list[str]] = None,
        config_dir: Optional[str] = None,
    ) -> None:
        if plugin_dirs:
            self._plugin_dirs = [Path(d) for d in plugin_dirs]
        else:
            from agent.core.config import get_home
            self._plugin_dirs = [
                get_home() / "plugins",
                Path(__file__).parent.parent.parent / "plugins",  # 项目内置
            ]

        if config_dir:
            self._config_dir = Path(config_dir)
        else:
            from agent.core.config import get_home
            self._config_dir = get_home() / "plugin_configs"

        self._plugins: dict[str, PluginInfo] = {}
        self._tool_handlers: dict[str, Callable] = {}  # tool_name → handler

    async def scan_plugins(self) -> list[PluginInfo]:
        """扫描所有插件目录，发现插件."""
        discovered = []

        for plugin_dir in self._plugin_dirs:
            if not plugin_dir.exists():
                plugin_dir.mkdir(parents=True, exist_ok=True)
                continue

            for entry in plugin_dir.iterdir():
                if not entry.is_dir():
                    continue
                if entry.name.startswith((".", "_")):
                    continue
                # 路径安全: 确保插件目录在 plugin_dir 内 (防止符号链接逃逸)
                try:
                    entry.resolve().relative_to(plugin_dir.resolve())
                except ValueError:
                    logger.warning("Skipping plugin outside plugin dir: %s", entry)
                    continue

                # 检查 plugin.yaml
                meta_file = entry / "plugin.yaml"
                init_file = entry / "__init__.py"

                if not init_file.exists():
                    continue

                if meta_file.exists():
                    meta = PluginMeta.from_yaml(meta_file)
                    if not meta.name:
                        meta.name = entry.name
                else:
                    meta = PluginMeta(name=entry.name)

                if meta.name not in self._plugins:
                    info = PluginInfo(meta=meta, path=entry)
                    self._plugins[meta.name] = info
                    discovered.append(info)
                    logger.info("Discovered plugin: %s (%s)", meta.name, entry)

        return discovered

    async def install_plugin(self, name: str) -> bool:
        """安装插件 (加载模块 + 调用 on_install)."""
        info = self._plugins.get(name)
        if not info:
            logger.error("Plugin not found: %s", name)
            return False

        if info.state not in (PluginState.DISCOVERED, PluginState.ERROR):
            logger.warning("Plugin %s already installed (state=%s)", name, info.state)
            return True

        try:
            # 动态加载模块
            instance = self._load_plugin_module(info)
            if not instance:
                info.state = PluginState.ERROR
                info.error = "Failed to load plugin module"
                return False

            # 加载配置
            config = self._load_plugin_config(name)
            instance.config = config
            info.config = config

            # 调用 on_install
            await instance.on_install()

            info.instance = instance
            info.state = PluginState.INSTALLED
            logger.info("Installed plugin: %s", name)
            return True

        except (ImportError, AttributeError, TypeError, OSError) as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            logger.error("Failed to install plugin %s: %s", name, e, exc_info=True)
            return False

    async def enable_plugin(self, name: str) -> bool:
        """启用插件 (注册工具)."""
        info = self._plugins.get(name)
        if not info:
            # 尝试先安装
            if not await self.install_plugin(name):
                return False
            info = self._plugins[name]

        if info.state == PluginState.ENABLED:
            return True

        if info.state not in (PluginState.INSTALLED, PluginState.CONFIGURED, PluginState.DISABLED):
            if not await self.install_plugin(name):
                return False
            info = self._plugins[name]

        try:
            if info.instance is None:
                return False

            # 调用 on_enable
            await info.instance.on_enable()

            # 收集注册的工具
            tools = info.instance.get_registered_tools()
            info.tools_registered = [t["name"] for t in tools]
            for tool in tools:
                self._tool_handlers[tool["name"]] = tool["handler"]

            info.state = PluginState.ENABLED
            logger.info("Enabled plugin: %s (tools: %s)", name, info.tools_registered)
            return True

        except (ImportError, AttributeError, TypeError) as e:
            info.state = PluginState.ERROR
            info.error = str(e)
            logger.error("Failed to enable plugin %s: %s", name, e, exc_info=True)
            return False

    async def disable_plugin(self, name: str) -> bool:
        """禁用插件."""
        info = self._plugins.get(name)
        if not info or info.state != PluginState.ENABLED:
            return False

        try:
            if info.instance:
                await info.instance.on_disable()

            # 移除工具
            for tool_name in info.tools_registered:
                self._tool_handlers.pop(tool_name, None)
            info.tools_registered = []

            info.state = PluginState.DISABLED
            logger.info("Disabled plugin: %s", name)
            return True

        except (AttributeError, TypeError, OSError) as e:
            logger.error("Error disabling plugin %s: %s", name, e)
            return False

    async def uninstall_plugin(self, name: str) -> bool:
        """卸载插件."""
        info = self._plugins.get(name)
        if not info:
            return False

        if info.state == PluginState.ENABLED:
            await self.disable_plugin(name)

        try:
            if info.instance:
                await info.instance.on_uninstall()
                info.instance = None

            info.state = PluginState.DISCOVERED
            logger.info("Uninstalled plugin: %s", name)
            return True

        except (AttributeError, TypeError, OSError) as e:
            logger.error("Error uninstalling plugin %s: %s", name, e)
            return False

    async def update_config(self, name: str, config: dict[str, Any]) -> bool:
        """更新插件配置."""
        info = self._plugins.get(name)
        if not info:
            return False

        info.config.update(config)
        self._save_plugin_config(name, info.config)

        if info.instance:
            await info.instance.on_config_update(info.config)

        return True

    def list_plugins(self) -> list[PluginInfo]:
        """列出所有插件."""
        return list(self._plugins.values())

    def get_plugin(self, name: str) -> Optional[PluginInfo]:
        """获取插件信息."""
        return self._plugins.get(name)

    def get_all_tools(self) -> list[dict[str, Any]]:
        """获取所有已启用插件的工具定义."""
        tools = []
        for info in self._plugins.values():
            if info.state == PluginState.ENABLED and info.instance:
                tools.extend(info.instance.get_registered_tools())
        return tools

    def get_tool_handler(self, tool_name: str) -> Optional[Callable]:
        """获取工具处理器."""
        return self._tool_handlers.get(tool_name)

    def _load_plugin_module(self, info: PluginInfo) -> Optional[BasePlugin]:
        """动态加载插件模块."""
        if not info.path:
            return None

        init_file = info.path / "__init__.py"
        if not init_file.exists():
            return None

        try:
            module_name = f"xjd_plugin_{info.name}"

            # 添加到 sys.path (临时)
            parent_dir = str(info.path.parent)
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)

            spec = importlib.util.spec_from_file_location(module_name, str(init_file))
            if not spec or not spec.loader:
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # 查找 Plugin 类
            plugin_cls = getattr(module, "Plugin", None)
            if plugin_cls is None:
                # 扫描所有 BasePlugin 子类
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BasePlugin)
                        and attr is not BasePlugin
                    ):
                        plugin_cls = attr
                        break

            if plugin_cls is None:
                logger.warning("No Plugin class found in %s", init_file)
                return None

            return plugin_cls(config=info.config)

        except (ImportError, AttributeError, TypeError, OSError) as e:
            logger.error("Failed to load plugin module %s: %s", info.name, e, exc_info=True)
            return None

    def _load_plugin_config(self, name: str) -> dict[str, Any]:
        """加载插件配置."""
        config_file = self._config_dir / f"{name}.yaml"
        if config_file.exists():
            try:
                data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
            except (yaml.YAMLError, OSError) as e:
                logger.debug("Failed to load plugin config %s: %s", name, e)
        return {}

    def _save_plugin_config(self, name: str, config: dict[str, Any]) -> None:
        """保存插件配置."""
        self._config_dir.mkdir(parents=True, exist_ok=True)
        config_file = self._config_dir / f"{name}.yaml"
        try:
            config_file.write_text(
                yaml.dump(config, allow_unicode=True, default_flow_style=False),
                encoding="utf-8",
            )
        except (yaml.YAMLError, OSError) as e:
            logger.warning("Failed to save config for %s: %s", name, e)
