"""技能管理工具 — Agent 可自主浏览/管理技能 (参考 HermesAgent).

三个工具:
- skills_list: 列出所有技能 (tier 1 元数据)
- skill_view: 查看技能完整内容 (tier 2)
- skill_manage: 创建/更新/删除技能
"""

from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 全局引用，由 register 时注入
_skill_manager = None


def _set_skill_manager(mgr) -> None:
    global _skill_manager
    _skill_manager = mgr


async def _skills_list(category: str = "", query: str = "", **kwargs) -> str:
    """列出所有技能 (tier 1 元数据)."""
    if not _skill_manager:
        return "错误: 技能管理器未初始化"

    skills = await _skill_manager.list_skills(category=category or None)

    if query:
        q = query.lower()
        skills = [s for s in skills if q in s.name.lower() or q in s.description.lower()
                  or any(q in t.lower() for t in s.tags)]

    if not skills:
        return "未找到技能"

    result = []
    for s in skills:
        result.append({
            "name": s.name,
            "description": s.description,
            "category": s.category,
            "tags": s.tags,
            "use_count": s.use_count,
            "skill_id": s.skill_id,
        })

    return json.dumps(result, ensure_ascii=False, indent=2)


async def _skill_view(name: str, **kwargs) -> str:
    """查看技能完整内容 (tier 2)."""
    if not _skill_manager:
        return "错误: 技能管理器未初始化"

    await _skill_manager._ensure_loaded()

    # 按 skill_id 或 name 查找
    for skill in _skill_manager._skills.values():
        if skill.skill_id == name or skill.name == name:
            return skill.to_skill_md()

    return f"未找到技能: {name}"


async def _skill_manage(action: str, name: str = "", content: str = "", conversation_summary: str = "", version: str = "", **kwargs) -> str:
    """创建/更新/删除/版本管理技能."""
    if not _skill_manager:
        return "错误: 技能管理器未初始化"

    if action == "create_from_chat":
        if not conversation_summary:
            return "错误: create_from_chat 需要 conversation_summary 参数（当前对话的摘要）"
        messages = [{"role": "user", "content": conversation_summary}]
        skill_md = await _skill_manager.extract_from_conversation(messages)
        if not skill_md:
            return "未能从对话中提取出可复用的技能。对话可能太简单或不包含工具调用流程。"
        from agent.skills.manager import Skill
        preview = Skill.from_skill_md(skill_md)
        return (
            f"从对话中提取了技能草稿，请确认或修改后保存:\n\n"
            f"```markdown\n{skill_md}\n```\n\n"
            f"技能名称: {preview.name}\n"
            f"描述: {preview.description}\n"
            f"状态: draft（需确认后激活）\n\n"
            f"如果满意，请用 skill_manage action=create 并传入上面的 content 来保存。\n"
            f"也可以告诉我需要修改的地方。"
        )

    elif action == "create":
        if not content:
            return "错误: create 需要 content 参数 (SKILL.md 格式)"
        from agent.skills.manager import Skill
        skill = Skill.from_skill_md(content)
        if not skill.name:
            return "错误: SKILL.md 缺少 name 字段"
        source = kwargs.get("source", skill.source or "manual")
        status = kwargs.get("status", skill.status or "active")
        created = await _skill_manager.create_skill(
            name=skill.name,
            description=skill.description,
            trigger=skill.trigger,
            body=skill.body,
            category=skill.category,
            tags=skill.tags,
            examples=skill.examples,
            source=source,
            status=status,
        )
        return f"技能已创建: {created.name} (ID: {created.skill_id}, 状态: {created.status})"

    elif action == "update":
        if not name or not content:
            return "错误: update 需要 name 和 content 参数"
        # 查找技能
        target = None
        for s in _skill_manager._skills.values():
            if s.skill_id == name or s.name == name:
                target = s
                break
        if not target:
            return f"未找到技能: {name}"
        from agent.skills.manager import Skill
        updated_skill = Skill.from_skill_md(content)
        updates = {}
        if updated_skill.name:
            updates["name"] = updated_skill.name
        if updated_skill.description:
            updates["description"] = updated_skill.description
        if updated_skill.trigger:
            updates["trigger"] = updated_skill.trigger
        if updated_skill.body:
            updates["body"] = updated_skill.body
        if updated_skill.tags:
            updates["tags"] = updated_skill.tags
        if updated_skill.examples:
            updates["examples"] = updated_skill.examples
        result = await _skill_manager.update_skill(target.skill_id, updates)
        return f"技能已更新: {result.name}" if result else "更新失败"

    elif action == "delete":
        if not name:
            return "错误: delete 需要 name 参数"
        # 查找
        target_id = None
        for s in _skill_manager._skills.values():
            if s.skill_id == name or s.name == name:
                target_id = s.skill_id
                break
        if not target_id:
            return f"未找到技能: {name}"
        ok = await _skill_manager.delete_skill(target_id)
        return f"技能已删除" if ok else "删除失败"

    elif action == "versions":
        return await _skill_versions(name)

    elif action == "rollback":
        return await _skill_rollback(name, version=version)

    elif action == "activate":
        if not name:
            return "错误: activate 需要 name 参数"
        skill_id = _resolve_skill_id(name)
        if not skill_id:
            return f"未找到技能: {name}"
        result = await _skill_manager.update_skill(skill_id, {"status": "active"})
        return f"技能 {result.name} 已激活" if result else "激活失败"

    return f"未知操作: {action} (可选: create/create_from_chat/update/delete/versions/rollback/activate)"


async def _skill_versions(name: str, **kwargs) -> str:
    """查看技能版本历史."""
    if not _skill_manager:
        return "错误: 技能管理器未初始化"
    skill_id = _resolve_skill_id(name)
    if not skill_id:
        return f"未找到技能: {name}"
    versions = await _skill_manager.list_versions(skill_id)
    if not versions:
        return f"技能 {name} 暂无版本历史。"
    from datetime import datetime
    lines = [f"技能 {name} 的版本历史:"]
    for v in reversed(versions):
        ts = datetime.fromtimestamp(v.get("updated_at", 0)).strftime("%Y-%m-%d %H:%M") if v.get("updated_at") else "?"
        lines.append(f"  v{v.get('version', '?')} | {ts} | {v.get('changelog', '')}")
    return "\n".join(lines)


async def _skill_rollback(name: str, version: str = "", **kwargs) -> str:
    """回滚技能到指定版本."""
    if not _skill_manager:
        return "错误: 技能管理器未初始化"
    if not version:
        return "错误: 必须指定要回滚到的版本号"
    skill_id = _resolve_skill_id(name)
    if not skill_id:
        return f"未找到技能: {name}"
    result = await _skill_manager.rollback_version(skill_id, version)
    if result:
        return f"技能 {result.name} 已回滚到 v{version}"
    return f"回滚失败: 版本 v{version} 不存在"


def _resolve_skill_id(name: str) -> str:
    """按 skill_id 或 name 查找，返回 skill_id."""
    if not _skill_manager:
        return ""
    for s in _skill_manager._skills.values():
        if s.skill_id == name or s.name == name:
            return s.skill_id
    return ""


def register_skill_tools(registry, skill_manager=None) -> None:
    """注册技能管理工具."""
    if skill_manager:
        _set_skill_manager(skill_manager)

    registry.register(
        name="skills_list",
        description="列出所有已学会的技能。可按分类或关键词过滤。",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "分类过滤 (可选)"},
                "query": {"type": "string", "description": "搜索关键词 (可选)"},
            },
            "required": [],
        },
        handler=_skills_list,
        category="skills",
    )

    registry.register(
        name="skill_view",
        description="查看技能的完整 SKILL.md 内容。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称或 ID"},
            },
            "required": ["name"],
        },
        handler=_skill_view,
        category="skills",
    )

    registry.register(
        name="skill_manage",
        description=(
            "创建、更新、删除或管理技能版本。\n"
            "action: create(创建) / create_from_chat(从对话提取) / update(更新) / delete(删除) / "
            "versions(版本历史) / rollback(回滚版本) / activate(激活draft技能)\n"
            "create_from_chat: 传入 conversation_summary，自动提取技能草稿\n"
            "content 使用 SKILL.md 格式 (YAML frontmatter + Markdown body)。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作",
                    "enum": ["create", "create_from_chat", "update", "delete", "versions", "rollback", "activate"],
                },
                "name": {"type": "string", "description": "技能名称或 ID"},
                "content": {"type": "string", "description": "SKILL.md 格式内容 (create/update)"},
                "conversation_summary": {"type": "string", "description": "对话摘要 (create_from_chat)"},
                "version": {"type": "string", "description": "版本号 (rollback)"},
                "source": {"type": "string", "description": "来源: manual/chat/hub"},
                "status": {"type": "string", "description": "状态: draft/active"},
            },
            "required": ["action"],
        },
        handler=_skill_manage,
        category="skills",
    )
