"""扩展工具集 — 兼容 shim.

所有工具已拆分到独立模块 (web_tools, code_tools, file_tools 等)。
此文件保留 register_extended_tools() 入口，内部委托给各子模块。

迁移完成后可删除此文件。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 子模块注册函数列表
_SUB_MODULES = [
    ("agent.tools.web_tools", "register_web_tools"),
    ("agent.tools.code_tools", "register_code_tools"),
    ("agent.tools.file_tools", "register_file_tools"),
    ("agent.tools.data_tools", "register_data_tools"),
    ("agent.tools.system_tools", "register_system_tools"),
    ("agent.tools.media_tools", "register_media_tools"),
    ("agent.tools.network_tools", "register_network_tools"),
    ("agent.tools.canvas_tools", "register_canvas_tools"),
    ("agent.tools.ecommerce_tools", "register_ecommerce_tools"),
    ("agent.tools.ecommerce_ops_tools", "register_ecommerce_ops_tools"),
    ("agent.tools.misc_tools", "register_misc_tools"),
]


def register_extended_tools(registry) -> None:
    """注册所有扩展工具 — 委托给各子模块."""
    total = 0
    for module_path, func_name in _SUB_MODULES:
        try:
            import importlib
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name)
            before = len(registry.list_tools())
            fn(registry)
            added = len(registry.list_tools()) - before
            total += added
            logger.debug("Loaded %d tools from %s", added, module_path)
        except Exception as e:
            logger.warning("Failed to load %s: %s", module_path, e)

    # ── 浏览器自动化 ──
    try:
        from agent.tools.browser import register_browser_tools
        register_browser_tools(registry)
    except Exception as e:
        logger.debug("Browser tools not available: %s", e)

    # ── 桌面控制 (Computer Use) ──
    try:
        from agent.tools.computer_use import register_computer_use_tools
        register_computer_use_tools(registry)
    except Exception as e:
        logger.debug("Computer use tools not available: %s", e)

    logger.info("Extended tools loaded: %d total", total)
