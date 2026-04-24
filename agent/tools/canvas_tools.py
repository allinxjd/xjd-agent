"""Canvas / A2UI 工具 — 创建、更新、导出交互式 UI 组件。

从 extended.py 提取，支持 HTML / Markdown / Mermaid / Chart.js / React。
"""

from __future__ import annotations

import base64
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
        "content": render_content,
    }, ensure_ascii=False)


async def _list_canvas(**kw) -> str:
    store = getattr(_canvas_mgr, '_store', None)
    if not store:
        items = [{"artifact_id": a.artifact_id, "type": a.canvas_type.value, "title": a.title} for a in _canvas_mgr.list_all()]
    else:
        items = store.list_artifacts() or []
    if not items:
        return "当前没有已保存的 Canvas。"
    return json.dumps({"canvases": items}, ensure_ascii=False)


async def _export_canvas(artifact_id: str, format: str = "html", **kw) -> str:
    from pathlib import Path
    from agent.core.canvas_export import CanvasExporter
    exporter = CanvasExporter(_canvas_mgr)
    try:
        if format == "pdf":
            data = await exporter.export_pdf(artifact_id)
            ext = "pdf"
        elif format == "png":
            data = await exporter.export_png(artifact_id)
            ext = "png"
        else:
            data = await exporter.export_html(artifact_id)
            ext = "html"
    except RuntimeError as e:
        return str(e)

    if data is None:
        return f"Canvas {artifact_id} 不存在"

    artifact = _canvas_mgr.get(artifact_id)
    title = artifact.title if artifact else "canvas"
    safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title).strip() or "canvas"
    filename = f"{safe_title}.{ext}"

    from agent.core.config import get_exports_dir
    export_dir = get_exports_dir()
    file_path = export_dir / filename
    file_path.write_bytes(data)

    return json.dumps({
        "__canvas_export__": True,
        "artifact_id": artifact_id,
        "format": format,
        "filename": filename,
        "file_path": str(file_path),
        "file_data": base64.b64encode(data).decode(),
        "size_bytes": len(data),
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

        registry.register(
            name="list_canvas",
            description="列出所有已保存的 Canvas 产物（含 ID、类型、标题）。在导出或更新前先调用此工具查找目标 Canvas。",
            parameters={"type": "object", "properties": {}},
            handler=_list_canvas,
            category="canvas",
        )

        registry.register(
            name="export_canvas",
            description="导出 Canvas 为文件 (HTML/PDF/PNG)，保存到 ~/.xjd-agent/exports/ 目录。需要先用 list_canvas 获取 artifact_id。在飞书/微信中会自动发送文件给用户。",
            parameters={
                "type": "object",
                "properties": {
                    "artifact_id": {"type": "string", "description": "Canvas ID"},
                    "format": {"type": "string", "description": "导出格式", "enum": ["html", "pdf", "png"]},
                },
                "required": ["artifact_id", "format"],
            },
            handler=_export_canvas,
            category="canvas",
        )
    except Exception as e:
        logger.debug("Canvas tools not available: %s", e)
