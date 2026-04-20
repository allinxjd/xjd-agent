"""工具注册表 — 统一管理所有工具的注册、查找、执行.

参考 HermesAgent (Nous Research) 的 ToolRegistry 设计:
- 每个工具文件导出 register_*_tools(registry) 函数
- discover_tools() 自动扫描并注册
- TOOLSETS 按场景分组工具，可动态组合
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent.providers.base import ToolDefinition

logger = logging.getLogger(__name__)

# ── 预定义 Toolset (参考 HermesAgent _HERMES_CORE_TOOLS + TOOLSETS) ──
TOOLSETS: dict[str, list[str]] = {
    "core": [
        "run_terminal", "read_file", "write_file", "edit_file", "list_directory",
        "web_search", "web_fetch", "execute_code", "grep_search",
        "create_canvas", "update_canvas",
        "generate_ecommerce_image", "request_user_approval",
        "download_file", "git_command",
    ],
    "web": ["web_search", "web_fetch", "download_file"],
    "code": ["execute_code", "grep_search", "git_command", "apply_patch"],
    "media": [
        "generate_ecommerce_image", "vision_analyze",
        "image_generate", "text_to_speech", "screenshot",
    ],
    "data": ["database_query", "json_query", "pdf_extract", "template_render"],
    "canvas": ["create_canvas", "update_canvas"],
    "skills": ["skills_list", "skill_view", "skill_manage"],
    "memory": ["memory_list", "memory_search", "memory_manage"],
    "system": ["system_info", "process_manager", "env_variable"],
}

@dataclass
class RegisteredTool:
    """已注册的工具."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    category: str = "general"
    requires_approval: bool = False
    timeout: float = 60.0  # 默认超时 60 秒
    retries: int = 0  # 默认不重试
    enabled: bool = True

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )

class ToolRegistry:
    """工具注册表.

    用法:
        registry = ToolRegistry()

        @registry.tool("run_terminal", "执行终端命令", {...})
        async def run_terminal(command: str) -> str:
            ...

        # 或手动注册
        registry.register("read_file", "读取文件", {...}, read_file_handler)
    """

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._toolsets: dict[str, set[str]] = {}
        self._tool_stats: dict[str, dict[str, Any]] = {}
        self._audit_logger: Optional[Any] = None
        # 速率限制: 令牌桶 (每分钟最大调用次数)
        self._rate_limit: int = 120  # 默认每分钟 120 次
        self._rate_tokens: float = 120.0
        self._rate_last_refill: float = 0.0
        # 加载预定义 toolset
        for name, tools in TOOLSETS.items():
            self._toolsets[name] = set(tools)

    def set_audit_logger(self, audit_logger: Any) -> None:
        """设置审计日志器."""
        self._audit_logger = audit_logger

    def set_rate_limit(self, calls_per_minute: int) -> None:
        """设置速率限制 (每分钟最大调用次数)."""
        self._rate_limit = calls_per_minute
        self._rate_tokens = float(calls_per_minute)

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Any],
        category: str = "general",
        requires_approval: bool = False,
        timeout: float = 60.0,
        retries: int = 0,
        optional_deps: Optional[list[str]] = None,
    ) -> None:
        """注册工具.

        Args:
            optional_deps: 可选依赖列表 (如 ["pdfplumber", "edge_tts"])。
                          如果任一依赖缺失，工具仍注册但标记为 disabled。
        """
        # 依赖检测
        missing_deps = []
        if optional_deps:
            for dep in optional_deps:
                try:
                    __import__(dep)
                except ImportError:
                    missing_deps.append(dep)

        enabled = len(missing_deps) == 0
        if missing_deps:
            logger.info("Tool %s disabled: missing deps %s", name, missing_deps)

        self._tools[name] = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            category=category,
            requires_approval=requires_approval,
            enabled=enabled,
            timeout=timeout,
            retries=retries,
        )
        logger.debug("Registered tool: %s [%s] enabled=%s", name, category, enabled)

    def tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        category: str = "general",
        requires_approval: bool = False,
    ) -> Callable:
        """装饰器方式注册工具."""

        def decorator(func: Callable) -> Callable:
            self.register(
                name=name,
                description=description,
                parameters=parameters,
                handler=func,
                category=category,
                requires_approval=requires_approval,
            )
            return func

        return decorator

    def get(self, name: str) -> Optional[RegisteredTool]:
        """获取工具."""
        return self._tools.get(name)

    def get_definitions(self, categories: Optional[list[str]] = None) -> list[ToolDefinition]:
        """获取所有已启用工具的定义 (用于传给模型)."""
        tools = []
        for t in self._tools.values():
            if not t.enabled:
                continue
            if categories and t.category not in categories:
                continue
            tools.append(t.definition)
        return tools

    def list_tools(self) -> list[RegisteredTool]:
        """列出所有工具."""
        return list(self._tools.values())

    def enable(self, name: str) -> None:
        if name in self._tools:
            self._tools[name].enabled = True

    def disable(self, name: str) -> None:
        if name in self._tools:
            self._tools[name].enabled = False

    def _validate_arguments(self, tool: RegisteredTool, arguments: dict[str, Any]) -> Optional[str]:
        """校验工具参数 (required 字段 + 基础类型检查)."""
        schema = tool.parameters
        if not schema or schema.get("type") != "object":
            return None
        # required 字段检查
        required = schema.get("required", [])
        for field in required:
            if field not in arguments:
                return f"Missing required parameter: '{field}'"
        # 基础类型检查
        props = schema.get("properties", {})
        type_map = {"string": str, "integer": int, "number": (int, float), "boolean": bool}
        for key, value in arguments.items():
            if key not in props:
                continue  # 允许额外参数 (kwargs)
            expected_type = props[key].get("type")
            if expected_type and expected_type in type_map:
                if not isinstance(value, type_map[expected_type]):
                    return f"Parameter '{key}' expected {expected_type}, got {type(value).__name__}"
        return None

    def _check_rate_limit(self) -> bool:
        """令牌桶速率限制检查."""
        import time
        now = time.monotonic()
        if self._rate_last_refill == 0:
            self._rate_last_refill = now
        # 补充令牌
        elapsed = now - self._rate_last_refill
        self._rate_tokens = min(
            float(self._rate_limit),
            self._rate_tokens + elapsed * (self._rate_limit / 60.0),
        )
        self._rate_last_refill = now
        # 消耗令牌
        if self._rate_tokens >= 1.0:
            self._rate_tokens -= 1.0
            return True
        return False

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        """执行工具 (带参数校验 + 速率限制 + 超时 + 重试 + 审计)."""
        import asyncio
        import time

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Unknown tool '{name}'"
        if not tool.enabled:
            return f"Error: Tool '{name}' is disabled"

        # 速率限制
        if not self._check_rate_limit():
            return f"Error: Rate limit exceeded ({self._rate_limit}/min)"

        # 参数校验
        validation_error = self._validate_arguments(tool, arguments)
        if validation_error:
            return f"Error: {validation_error}"

        last_error = None
        attempts = 1 + tool.retries

        for attempt in range(attempts):
            start = time.monotonic()
            try:
                result = tool.handler(**arguments)
                if hasattr(result, "__await__"):
                    result = await asyncio.wait_for(result, timeout=tool.timeout)
                elapsed = (time.monotonic() - start) * 1000
                logger.debug("Tool %s completed in %.0fms", name, elapsed)

                # 统计
                stats = self._tool_stats.setdefault(name, {"calls": 0, "errors": 0, "total_ms": 0.0})
                stats["calls"] += 1
                stats["total_ms"] += elapsed

                # Evict oldest half if stats dict grows too large
                if len(self._tool_stats) > 10000:
                    sorted_keys = sorted(self._tool_stats, key=lambda k: self._tool_stats[k]["calls"])
                    for k in sorted_keys[:len(sorted_keys) // 2]:
                        del self._tool_stats[k]

                # 审计日志
                if self._audit_logger:
                    try:
                        await self._audit_logger.log(
                            action=f"tool:{name}",
                            detail=f"ok {elapsed:.0f}ms",
                        )
                    except Exception:
                        pass

                return str(result) if result is not None else "OK"

            except asyncio.TimeoutError:
                elapsed = (time.monotonic() - start) * 1000
                last_error = f"Timeout after {tool.timeout}s"
                logger.warning("Tool %s timed out (attempt %d/%d, %.0fms)",
                              name, attempt + 1, attempts, elapsed)
            except Exception as e:
                elapsed = (time.monotonic() - start) * 1000
                last_error = str(e)
                logger.error("Tool %s failed (attempt %d/%d, %.0fms): %s",
                            name, attempt + 1, attempts, elapsed, e, exc_info=True)

            # 重试前等待
            if attempt < attempts - 1:
                await asyncio.sleep(min(2 ** attempt, 5))

        # 所有重试都失败
        stats = self._tool_stats.setdefault(name, {"calls": 0, "errors": 0, "total_ms": 0.0})
        stats["calls"] += 1
        stats["errors"] += 1

        # 审计日志 (失败)
        if self._audit_logger:
            try:
                await self._audit_logger.log(
                    action=f"tool:{name}",
                    detail=f"FAIL: {last_error}",
                )
            except Exception:
                pass

        return f"Error: {last_error}"

    def get_tool_stats(self) -> dict[str, dict[str, Any]]:
        """获取工具执行统计."""
        return dict(self._tool_stats)

    def get_metrics(self) -> dict[str, Any]:
        """获取结构化可观测性指标 — 用于监控和告警."""
        total_calls = sum(s["calls"] for s in self._tool_stats.values())
        total_errors = sum(s["errors"] for s in self._tool_stats.values())
        total_ms = sum(s["total_ms"] for s in self._tool_stats.values())

        # 按工具排序: 调用次数最多的在前
        top_tools = sorted(
            self._tool_stats.items(),
            key=lambda x: x[1]["calls"],
            reverse=True,
        )[:10]

        return {
            "total_tools": len(self._tools),
            "enabled_tools": sum(1 for t in self._tools.values() if t.enabled),
            "disabled_tools": sum(1 for t in self._tools.values() if not t.enabled),
            "total_calls": total_calls,
            "total_errors": total_errors,
            "error_rate": total_errors / total_calls if total_calls else 0.0,
            "avg_latency_ms": total_ms / total_calls if total_calls else 0.0,
            "top_tools": [
                {
                    "name": name,
                    "calls": stats["calls"],
                    "errors": stats["errors"],
                    "avg_ms": stats["total_ms"] / stats["calls"] if stats["calls"] else 0,
                }
                for name, stats in top_tools
            ],
        }

    # ── Toolset 组合 ──

    def create_toolset(self, name: str, tool_names: list[str]) -> None:
        """创建工具集 — 按场景组合工具."""
        self._toolsets[name] = set(tool_names)

    def apply_toolset(self, name: str) -> int:
        """应用工具集 — 只启用指定集合中的工具，其余禁用."""
        ts = self._toolsets.get(name)
        if ts is None:
            return -1
        count = 0
        for tool_name, tool in self._tools.items():
            tool.enabled = tool_name in ts
            if tool.enabled:
                count += 1
        return count

    def reset_toolset(self) -> None:
        """重置 — 启用所有工具."""
        for tool in self._tools.values():
            tool.enabled = True

    def list_toolsets(self) -> dict[str, list[str]]:
        """列出所有工具集."""
        return {name: sorted(tools) for name, tools in self._toolsets.items()}

    def list_by_category(self, category: str) -> list[RegisteredTool]:
        """按分类列出工具."""
        return [t for t in self._tools.values() if t.category == category]

    def apply_allow_list(self, allowed: list[str]) -> int:
        """应用工具白名单 — 只启用列表中的工具 ."""
        count = 0
        for name, tool in self._tools.items():
            tool.enabled = name in allowed
            if tool.enabled:
                count += 1
        logger.info("应用工具白名单: %d/%d 已启用", count, len(self._tools))
        return count

    def apply_deny_list(self, denied: list[str]) -> int:
        """应用工具黑名单 — 禁用列表中的工具."""
        count = 0
        for name, tool in self._tools.items():
            if name in denied:
                tool.enabled = False
                count += 1
        logger.info("应用工具黑名单: %d 已禁用", count)
        return count

    def get_categories(self) -> list[str]:
        """获取所有工具分类."""
        return sorted(set(t.category for t in self._tools.values()))

    # ── Toolset 组合 (参考 HermesAgent compose 模式) ──

    def compose_toolsets(self, *names: str) -> list[ToolDefinition]:
        """组合多个 toolset，返回合并后的工具定义列表.

        用法: registry.compose_toolsets("core", "skills")
        """
        combined: set[str] = set()
        for name in names:
            ts = self._toolsets.get(name)
            if ts:
                combined |= ts
        return [
            t.definition for t in self._tools.values()
            if t.name in combined and t.enabled
        ]

    def get_definitions_by_toolset(self, toolset: str) -> list[ToolDefinition]:
        """按 toolset 名获取工具定义."""
        ts = self._toolsets.get(toolset)
        if not ts:
            return []
        return [
            t.definition for t in self._tools.values()
            if t.name in ts and t.enabled
        ]

    def discover_tools(self, package_path: str = "agent.tools") -> int:
        """自动发现并注册工具模块.

        扫描 package_path 下所有 *_tools.py 模块，
        调用其 register_*_tools(registry) 函数。

        参考 HermesAgent 的 discover_builtin_tools() 设计。

        Returns:
            注册的模块数量
        """
        count = 0
        try:
            package = importlib.import_module(package_path)
        except ImportError:
            logger.warning("Cannot import package: %s", package_path)
            return 0

        if not hasattr(package, "__path__"):
            return 0

        for importer, modname, ispkg in pkgutil.iter_modules(package.__path__):
            if not modname.endswith("_tools") or modname == "extended":
                continue
            full_name = f"{package_path}.{modname}"
            try:
                mod = importlib.import_module(full_name)
                # 查找 register_*_tools 函数
                register_fn_name = f"register_{modname.replace('_tools', '')}_tools"
                # 也尝试完整名称 register_xxx_tools
                for attr_name in [register_fn_name, f"register_{modname}"]:
                    fn = getattr(mod, attr_name, None)
                    if callable(fn):
                        fn(self)
                        count += 1
                        logger.info("Auto-discovered tools from: %s", full_name)
                        break
                else:
                    logger.debug("No register function found in: %s", full_name)
            except Exception as e:
                logger.warning("Failed to load tool module %s: %s", full_name, e)
        return count

# 全局工具注册表
default_registry = ToolRegistry()
