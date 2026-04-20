"""记忆管理工具 — Agent 可自主读写记忆 (参考 Letta 模式).

三个工具:
- memory_list: 列出记忆
- memory_search: 搜索记忆 (语义 + 关键词)
- memory_manage: 创建/更新/删除记忆
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_memory_manager = None


def _set_memory_manager(mgr) -> None:
    global _memory_manager
    _memory_manager = mgr


async def _memory_list(
    memory_type: str = "",
    user_id: str = "default",
    limit: int = 50,
    **kwargs,
) -> str:
    """列出记忆."""
    if not _memory_manager:
        return "错误: 记忆管理器未初始化"

    from agent.memory.provider import MemoryType

    mt = None
    if memory_type:
        try:
            mt = MemoryType(memory_type)
        except ValueError:
            return f"错误: 未知记忆类型 '{memory_type}'。可选: {[t.value for t in MemoryType]}"

    memories = await _memory_manager.list_memories(user_id=user_id, memory_type=mt)
    memories = memories[:limit]

    if not memories:
        return "暂无记忆"

    result = []
    for m in memories:
        result.append({
            "memory_id": m.memory_id,
            "content": m.content,
            "memory_type": m.memory_type.value,
            "importance": m.importance.value,
            "tags": m.tags,
            "access_count": m.access_count,
        })

    return json.dumps(result, ensure_ascii=False, indent=2)


async def _memory_search(
    query: str,
    user_id: str = "default",
    limit: int = 10,
    **kwargs,
) -> str:
    """搜索记忆 (语义 + 关键词)."""
    if not _memory_manager:
        return "错误: 记忆管理器未初始化"

    if not query.strip():
        return "错误: query 不能为空"

    results = await _memory_manager.recall(query=query, user_id=user_id, limit=limit)

    if not results:
        return f"未找到与 \"{query}\" 相关的记忆"

    output = []
    for r in results:
        output.append({
            "content": r.memory.content,
            "memory_type": r.memory.memory_type.value,
            "relevance_score": round(r.relevance_score, 3),
            "match_type": r.match_type,
            "memory_id": r.memory.memory_id,
        })

    return json.dumps(output, ensure_ascii=False, indent=2)


async def _memory_manage(
    action: str,
    content: str = "",
    memory_id: str = "",
    memory_type: str = "fact",
    importance: str = "medium",
    tags: str = "",
    user_id: str = "default",
    **kwargs,
) -> str:
    """创建/更新/删除记忆."""
    if not _memory_manager:
        return "错误: 记忆管理器未初始化"

    from agent.memory.provider import MemoryImportance, MemoryType

    if action == "create":
        if not content:
            return "错误: create 需要 content 参数"
        try:
            mt = MemoryType(memory_type)
        except ValueError:
            mt = MemoryType.FACT
        try:
            imp = MemoryImportance(importance)
        except ValueError:
            imp = MemoryImportance.MEDIUM

        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

        mid = await _memory_manager.remember(
            content=content,
            user_id=user_id,
            memory_type=mt,
            importance=imp,
            tags=tag_list,
        )
        return f"记忆已创建 (ID: {mid[:8]})"

    elif action == "update":
        if not memory_id:
            return "错误: update 需要 memory_id 参数"
        updates = {}
        if content:
            updates["content"] = content
        if importance:
            updates["importance"] = importance
        if not updates:
            return "错误: update 至少需要 content 或 importance"

        provider = _memory_manager._provider
        ok = await provider.update(memory_id, updates)
        return "记忆已更新" if ok else f"更新失败: 未找到 ID {memory_id[:8]}"

    elif action == "delete":
        if not memory_id:
            return "错误: delete 需要 memory_id 参数"
        ok = await _memory_manager.forget(memory_id)
        return "记忆已删除" if ok else f"删除失败: 未找到 ID {memory_id[:8]}"

    return f"未知操作: {action} (可选: create/update/delete)"


def register_memory_tools(registry, memory_manager=None) -> None:
    """注册记忆管理工具."""
    if memory_manager:
        _set_memory_manager(memory_manager)

    registry.register(
        name="memory_list",
        description="列出已存储的记忆。可按类型过滤。",
        parameters={
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "description": "记忆类型过滤",
                    "enum": ["fact", "preference", "skill", "episodic", "context", "relationship"],
                },
                "user_id": {"type": "string", "description": "用户 ID (默认 default)"},
                "limit": {"type": "integer", "description": "最大返回数 (默认 50)"},
            },
            "required": [],
        },
        handler=_memory_list,
        category="memory",
    )

    registry.register(
        name="memory_search",
        description="搜索记忆 (支持语义搜索和关键词搜索)。用于回忆用户信息、偏好、历史上下文。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "user_id": {"type": "string", "description": "用户 ID (默认 default)"},
                "limit": {"type": "integer", "description": "最大返回数 (默认 10)"},
            },
            "required": ["query"],
        },
        handler=_memory_search,
        category="memory",
    )

    registry.register(
        name="memory_manage",
        description="创建、更新或删除记忆。用于主动记住重要信息或清理过时记忆。",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["create", "update", "delete"],
                },
                "content": {"type": "string", "description": "记忆内容 (create/update)"},
                "memory_id": {"type": "string", "description": "记忆 ID (update/delete)"},
                "memory_type": {
                    "type": "string",
                    "description": "记忆类型 (默认 fact)",
                    "enum": ["fact", "preference", "skill", "episodic", "context"],
                },
                "importance": {
                    "type": "string",
                    "description": "重要度 (默认 medium)",
                    "enum": ["low", "medium", "high", "critical"],
                },
                "tags": {"type": "string", "description": "标签 (逗号分隔)"},
            },
            "required": ["action"],
        },
        handler=_memory_manage,
        category="memory",
    )
