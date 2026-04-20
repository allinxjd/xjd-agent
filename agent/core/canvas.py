"""Canvas / A2UI — Agent 生成交互式 UI (支持 Agent 动态生成可交互的 UI 组件:
- HTML: 原生 HTML 页面
- Markdown: 富文本渲染
- Mermaid: 流程图/时序图
- Chart: Chart.js 图表
- React: 简单 React 组件

用法:
    manager = CanvasManager()
    artifact = manager.create(CanvasType.MERMAID, "流程图", "graph TD; A-->B")
    html = manager.render_html(artifact.artifact_id)
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

class CanvasType(str, Enum):
    """Canvas 类型."""
    HTML = "html"
    MARKDOWN = "markdown"
    MERMAID = "mermaid"
    CHART = "chart"
    REACT = "react"

@dataclass
class CanvasArtifact:
    """Canvas 产物."""

    artifact_id: str = ""
    canvas_type: CanvasType = CanvasType.HTML
    title: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

class CanvasManager:
    """Canvas 管理器."""

    def __init__(self) -> None:
        self._artifacts: dict[str, CanvasArtifact] = {}
        self._listeners: list[Callable[[str, CanvasArtifact], Any]] = []

    def on_change(self, callback: Callable[[str, CanvasArtifact], Any]) -> None:
        """注册变更监听器. callback(event, artifact)."""
        self._listeners.append(callback)

    def create(
        self, canvas_type: CanvasType, title: str, content: str,
        metadata: Optional[dict] = None,
    ) -> CanvasArtifact:
        """创建 Canvas 产物."""
        now = time.time()
        artifact = CanvasArtifact(
            artifact_id=uuid.uuid4().hex[:12],
            canvas_type=canvas_type,
            title=title,
            content=content,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        self._artifacts[artifact.artifact_id] = artifact
        self._notify("create", artifact)
        return artifact

    def update(self, artifact_id: str, content: str, metadata: Optional[dict] = None) -> Optional[CanvasArtifact]:
        """更新 Canvas 内容."""
        artifact = self._artifacts.get(artifact_id)
        if not artifact:
            return None
        artifact.content = content
        artifact.updated_at = time.time()
        if metadata:
            artifact.metadata.update(metadata)
        self._notify("update", artifact)
        return artifact

    def get(self, artifact_id: str) -> Optional[CanvasArtifact]:
        return self._artifacts.get(artifact_id)

    def list_all(self) -> list[CanvasArtifact]:
        return list(self._artifacts.values())

    def delete(self, artifact_id: str) -> bool:
        artifact = self._artifacts.pop(artifact_id, None)
        if artifact:
            self._notify("delete", artifact)
            return True
        return False

    def render_html(self, artifact_id: str) -> Optional[str]:
        """渲染为完整 HTML 页面."""
        artifact = self._artifacts.get(artifact_id)
        if not artifact:
            return None

        renderers = {
            CanvasType.HTML: self._render_raw_html,
            CanvasType.MARKDOWN: self._render_markdown,
            CanvasType.MERMAID: self._render_mermaid,
            CanvasType.CHART: self._render_chart,
            CanvasType.REACT: self._render_react,
        }
        renderer = renderers.get(artifact.canvas_type, self._render_raw_html)
        return renderer(artifact)

    def _render_raw_html(self, a: CanvasArtifact) -> str:
        return _wrap_html(a.title, a.content)

    def _render_markdown(self, a: CanvasArtifact) -> str:
        body = f"""<div id="content">{a.content}</div>
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>document.getElementById('content').innerHTML=marked.parse(document.getElementById('content').textContent);</script>"""
        return _wrap_html(a.title, body)

    def _render_mermaid(self, a: CanvasArtifact) -> str:
        body = f"""<pre class="mermaid">{a.content}</pre>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true}});</script>"""
        return _wrap_html(a.title, body)

    def _render_chart(self, a: CanvasArtifact) -> str:
        body = f"""<canvas id="chart"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>new Chart(document.getElementById('chart'),{a.content});</script>"""
        return _wrap_html(a.title, body)

    def _render_react(self, a: CanvasArtifact) -> str:
        body = f"""<div id="root"></div>
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script type="text/babel">{a.content}</script>"""
        return _wrap_html(a.title, body)

    def _notify(self, event: str, artifact: CanvasArtifact) -> None:
        for cb in self._listeners:
            try:
                cb(event, artifact)
            except Exception as e:
                logger.warning("Canvas listener error: %s", e)

def _wrap_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;margin:0;padding:2.5rem 3rem;line-height:1.8;color:#1a1a1a;background:#fff}}
h1{{font-size:1.8rem;margin:0 0 1rem}}
h2{{font-size:1.4rem;margin:1.5rem 0 0.8rem}}
h3{{font-size:1.15rem;margin:1.2rem 0 0.6rem}}
p{{margin:0.8rem 0}}
ul,ol{{padding-left:1.5rem;margin:0.8rem 0}}
li{{margin:0.4rem 0}}
table{{border-collapse:collapse;width:100%;margin:1rem 0}}
th,td{{border:1px solid #e0e0e0;padding:10px 14px;text-align:left}}
th{{background:#f5f5f5;font-weight:600}}
img{{max-width:100%;height:auto;border-radius:8px}}
code{{background:#f4f4f4;padding:2px 6px;border-radius:4px;font-size:0.9em}}
pre{{background:#f4f4f4;padding:1rem;border-radius:8px;overflow-x:auto}}
</style>
</head>
<body>{body}</body>
</html>"""

# 公开别名，供外部调用
wrap_canvas_html = _wrap_html
