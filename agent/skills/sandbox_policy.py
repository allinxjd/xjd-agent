"""技能沙箱策略 — 工具风险分级 + 权限强制执行.

在技能激活期间，只允许技能声明的工具被调用。
未声明的工具调用会被拦截并返回错误。
"""

from __future__ import annotations

DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "run_terminal",
    "write_file",
    "edit_file",
    "execute_code",
    "git_command",
    "process_manager",
    "database_query",
    "apply_patch",
})

MODERATE_TOOLS: frozenset[str] = frozenset({
    "web_fetch",
    "download_file",
    "template_render",
})

SAFE_TOOLS: frozenset[str] = frozenset({
    "web_search",
    "read_file",
    "list_directory",
    "grep_search",
    "memory_search",
    "memory_list",
    "skills_list",
    "skill_view",
    "vision_analyze",
    "create_canvas",
    "update_canvas",
    "request_user_approval",
    "json_query",
    "system_info",
})


def assess_tools_risk(tools: list[str]) -> str:
    """评估技能声明的工具风险等级."""
    tool_set = set(tools)
    if tool_set & DANGEROUS_TOOLS:
        return "high"
    if tool_set & MODERATE_TOOLS:
        return "medium"
    return "low"
