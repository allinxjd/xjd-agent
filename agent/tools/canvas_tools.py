"""Canvas / A2UI 工具 — 创建和更新交互式 UI 组件。

从 extended.py 提取，支持 HTML / Markdown / Mermaid / Chart.js / React。
"""

from __future__ import annotations

import json
import logging

from agent.core.canvas import CanvasManager, CanvasType, wrap_canvas_html

logger = logging.getLogger(__name__)

_canvas_mgr = CanvasManager()


async def _create_canvas(type: str = "html", title: str = "", content: str = "", **kw) -> str:
    try:
        ct = CanvasType(type)
    except ValueError:
        return f"不支持的类型: {type}, 可选: {', '.join(t.value for t in CanvasType)}"
    # 如果是 HTML 片段，包装完整页面样式
    render_content = content
    if ct == CanvasType.HTML and '<html' not in content.lower()[:200]:
        render_content = wrap_canvas_html(title, content)
    a = _canvas_mgr.create(ct, title, content)
    return json.dumps({
        "__canvas_render__": True,
        "artifact_id": a.artifact_id,
        "type": a.canvas_type.value,
        "title": a.title,
        "content": render_content,
    }, ensure_ascii=False)


async def _update_canvas(artifact_id: str, content: str, **kw) -> str:
    a = _canvas_mgr.update(artifact_id, content)
    if not a:
        return f"Canvas {artifact_id} 不存在"
    render_content = content
    if a.canvas_type == CanvasType.HTML and '<html' not in content.lower()[:200]:
        render_content = wrap_canvas_html(a.title, content)
    return json.dumps({
        "__canvas_render__": True,
        "artifact_id": a.artifact_id,
        "type": a.canvas_type.value,
        "title": a.title,
        "content": a.content,
    }, ensure_ascii=False)


def register_canvas_tools(registry) -> None:
    """Register canvas tools with the given tool registry."""
    try:
        registry.register(
            name="create_canvas",
            description="创建交互式 UI 组件 (HTML/Markdown/Mermaid 图表/Chart.js 图表/React)。",
            parameters={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "类型", "enum": ["html", "markdown", "mermaid", "chart", "react"]},
                    "title": {"type": "string", "description": "标题"},
                    "content": {"type": "string", "description": "内容"},
                },
                "required": ["type", "title", "content"],
            },
            handler=_create_canvas,
            category="canvas",
        )

        registry.register(
            name="update_canvas",
            description="更新已有的 Canvas 内容。",
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "description": "Canvas ID"},
                    "content": {"type": "string", "description": "新内容"},
                },
                "required": ["artifact_id", "content"],
            },
            handler=_update_canvas,
            category="canvas",
        )
    except Exception as e:
        logger.debug("Canvas tools not available: %s", e)
