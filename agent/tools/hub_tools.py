"""XjdHub Agent 工具 — 用户通过聊天搜索/安装/发布技能."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_hub_client = None
_skill_manager = None


def register_hub_tools(registry: Any, hub_client: Any = None, skill_manager: Any = None) -> None:
    global _hub_client, _skill_manager
    if hub_client:
        _hub_client = hub_client
    else:
        try:
            from agent.skills.marketplace import HubClient
            _hub_client = HubClient()
        except Exception as e:
            logger.debug("HubClient init failed: %s", e)
    _skill_manager = skill_manager

    async def xjdhub_search(query: str = "", category: str = "", **kw) -> str:
        if not _hub_client:
            return "错误: Hub 客户端未初始化"
        try:
            results = await _hub_client.search(query=query, category=category)
        except Exception as e:
            return f"搜索失败: {e}"
        if not results:
            return f"未找到匹配「{query}」的技能。"
        lines = ["XjdHub 技能搜索结果:\n"]
        for r in results:
            price_tag = f"¥{r.price}" if r.price > 0 else "免费"
            lines.append(
                f"- {r.name} v{r.version} ({price_tag})\n"
                f"  {r.description}\n"
                f"  下载: {r.downloads} | 标签: {', '.join(r.tags)}"
            )
        return "\n".join(lines)

    async def xjdhub_install(name: str, version: str = "latest", confirmed: bool = False, **kw) -> str:
        if not _hub_client:
            return "错误: Hub 客户端未初始化"
        if not confirmed:
            return (
                f"即将安装技能「{name}」(版本: {version})。\n"
                f"安装后该技能将可被 Agent 调用。\n"
                f"请确认安装，再次调用 xjdhub_install 并设置 confirmed=true。"
            )
        try:
            result = await _hub_client.install(name)
        except Exception as e:
            return f"安装失败: {e}"
        if result.success:
            return f"技能安装成功: {name}"
        return f"安装失败: {result.message}"

    async def xjdhub_publish(skill_id: str, confirmed: bool = False, **kw) -> str:
        if not _hub_client:
            return "错误: Hub 客户端未初始化"
        if not confirmed:
            return (
                f"即将发布技能「{skill_id}」到 XjdHub 技能市场。\n"
                f"发布后其他用户可以搜索和安装此技能。\n"
                f"请确认发布，再次调用 xjdhub_publish 并设置 confirmed=true。"
            )
        try:
            result = await _hub_client.publish(skill_id)
        except Exception as e:
            return f"发布失败: {e}"
        if result.success:
            return f"技能已发布到 XjdHub: {result.slug} (待审核)"
        return f"发布失败: {result.message}"

    async def xjdhub_info(name: str, **kw) -> str:
        if not _hub_client:
            return "错误: Hub 客户端未初始化"
        try:
            info = await _hub_client.get_skill(name)
        except Exception as e:
            return f"查询失败: {e}"
        if not info:
            return f"未找到技能: {name}"
        return json.dumps(info, ensure_ascii=False, indent=2)

    registry.register(
        name="xjdhub_search",
        description="搜索 XjdHub 技能市场。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "category": {"type": "string", "description": "分类过滤"},
            },
            "required": [],
        },
        handler=xjdhub_search,
        category="skills",
    )

    registry.register(
        name="xjdhub_install",
        description="从 XjdHub 安装技能到本地。首次调用会要求确认，confirmed=true 时执行安装。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称或 slug"},
                "version": {"type": "string", "description": "版本号 (默认 latest)"},
                "confirmed": {"type": "boolean", "description": "是否已确认安装"},
            },
            "required": ["name"],
        },
        handler=xjdhub_install,
        category="skills",
    )

    registry.register(
        name="xjdhub_publish",
        description="将本地技能发布到 XjdHub 技能市场。首次调用会要求确认，confirmed=true 时执行发布。",
        parameters={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "要发布的技能路径或 ID"},
                "confirmed": {"type": "boolean", "description": "是否已确认发布"},
            },
            "required": ["skill_id"],
        },
        handler=xjdhub_publish,
        category="skills",
    )

    registry.register(
        name="xjdhub_info",
        description="查看 XjdHub 技能详情。",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称或 slug"},
            },
            "required": ["name"],
        },
        handler=xjdhub_info,
        category="skills",
    )

    logger.info("Registered XjdHub tools: search, install, publish, info")
