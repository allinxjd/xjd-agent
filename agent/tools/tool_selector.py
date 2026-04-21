"""意图→工具集映射 — 零 LLM 调用的关键词匹配器.

参考 Hermes Agent 的 toolset 动态过滤 + OpenClaw 的 sandbox-tool-policy，
在发送给模型之前裁剪工具列表，减少 token 消耗和错误工具调用。

当无技能激活时，根据用户消息关键词选择相关 toolset，
返回 ~8-12 个工具而非全量 21 个。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_INTENT_RULES: list[tuple[set[str], list[str], set[str]]] = [
    # (关键词集合, toolset 名列表, 额外单独工具)
    # 搜索/资讯
    (
        {"搜索", "查找", "新闻", "资讯", "search", "fetch", "网页", "url", "查询"},
        ["web"],
        set(),
    ),
    # 代码/终端
    (
        {"代码", "运行", "执行", "终端", "terminal", "code", "git", "编程",
         "debug", "脚本", "命令", "shell", "pip", "npm", "python"},
        ["code"],
        {"run_terminal", "execute_code"},
    ),
    # 画布/可视化
    (
        {"画布", "canvas", "知识图谱", "可视化", "导出", "export", "pdf", "png",
         "mermaid", "chart", "图表", "页面"},
        ["canvas"],
        set(),
    ),
    # 文件操作
    (
        {"文件", "读取", "写入", "file", "read", "write", "目录", "directory",
         "创建文件", "编辑文件", "删除"},
        [],
        {"read_file", "write_file", "edit_file", "list_directory", "grep_search"},
    ),
    # 图片/媒体
    (
        {"图片", "图像", "image", "语音", "tts", "截图", "screenshot",
         "电商图", "主图", "白底图", "详情图", "种草图"},
        ["media"],
        set(),
    ),
    # 数据
    (
        {"数据库", "database", "json", "pdf", "模板", "sql"},
        ["data"],
        set(),
    ),
    # 记忆
    (
        {"记忆", "memory", "记住", "忘记"},
        ["memory"],
        set(),
    ),
]

_ALWAYS_INCLUDE: set[str] = {
    "request_user_approval",
    "read_file",
    "list_directory",
}


def select_tool_names_for_message(message: str) -> Optional[set[str]]:
    """根据用户消息关键词选择相关工具名集合.

    Returns:
        匹配到 → 返回工具名集合
        匹配 0 个或 ≥4 个意图（模糊）→ 返回 None（fallback 到全量）
    """
    from agent.tools.registry import TOOLSETS

    msg_lower = message.lower()
    matched_toolsets: set[str] = set()
    extra_tools: set[str] = set()

    for keywords, toolset_names, extra in _INTENT_RULES:
        if any(kw in msg_lower for kw in keywords):
            matched_toolsets.update(toolset_names)
            extra_tools.update(extra)

    if not matched_toolsets and not extra_tools:
        return None
    if len(matched_toolsets) >= 4:
        return None

    names: set[str] = set(_ALWAYS_INCLUDE) | extra_tools
    for ts_name in matched_toolsets:
        ts = TOOLSETS.get(ts_name)
        if ts:
            names.update(ts)

    logger.debug(
        "Tool selector: matched toolsets=%s, total tools=%d",
        sorted(matched_toolsets), len(names),
    )
    return names
