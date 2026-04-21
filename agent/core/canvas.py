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

import html as _html
import logging
import threading
import time
import uuid
from collections import OrderedDict
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

    MAX_CACHED = 200

    def __init__(self, persist: bool = True) -> None:
        self._artifacts: OrderedDict[str, CanvasArtifact] = OrderedDict()
        self._lock = threading.Lock()
        self._listeners: list[Callable[[str, CanvasArtifact], Any]] = []
        self._store = None
        if persist:
            try:
                from .canvas_store import CanvasStore
                self._store = CanvasStore()
            except Exception:
                logger.debug("Canvas persistence unavailable", exc_info=True)

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
        with self._lock:
            self._artifacts[artifact.artifact_id] = artifact
            self._evict()
        self._notify("create", artifact)
        if self._store:
            try:
                self._store.save(artifact)
            except Exception:
                logger.debug("Canvas persist failed", exc_info=True)
        return artifact

    def update(self, artifact_id: str, content: str, metadata: Optional[dict] = None) -> Optional[CanvasArtifact]:
        """更新 Canvas 内容."""
        artifact = self.get(artifact_id)
        if not artifact:
            return None
        with self._lock:
            artifact.content = content
            artifact.updated_at = time.time()
            if metadata:
                artifact.metadata.update(metadata)
            self._artifacts.move_to_end(artifact_id)
        self._notify("update", artifact)
        if self._store:
            try:
                self._store.save(artifact)
            except Exception:
                logger.debug("Canvas persist failed", exc_info=True)
        return artifact

    def get(self, artifact_id: str) -> Optional[CanvasArtifact]:
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
            if artifact:
                self._artifacts.move_to_end(artifact_id)
                return artifact
        if self._store:
            artifact = self._store.load(artifact_id)
            if artifact:
                with self._lock:
                    self._artifacts[artifact_id] = artifact
                    self._evict()
        return artifact

    def list_all(self) -> list[CanvasArtifact]:
        with self._lock:
            return list(self._artifacts.values())

    def delete(self, artifact_id: str) -> bool:
        with self._lock:
            artifact = self._artifacts.pop(artifact_id, None)
        if artifact:
            self._notify("delete", artifact)
            if self._store:
                try:
                    self._store.delete(artifact_id)
                except Exception:
                    pass
            return True
        return False

    def _evict(self) -> None:
        while len(self._artifacts) > self.MAX_CACHED:
            self._artifacts.popitem(last=False)

    def render_html(self, artifact_id: str) -> Optional[str]:
        """渲染为完整 HTML 页面."""
        artifact = self.get(artifact_id)
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
<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js" onerror="document.getElementById('_cdn_err').style.display=''"></script>
<script>if(typeof marked!=='undefined')document.getElementById('content').innerHTML=marked.parse(document.getElementById('content').textContent);</script>
<div id="_cdn_err" style="display:none;color:#c00;padding:8px;font-size:13px">⚠ Markdown 渲染库加载失败（需要网络），已显示原始内容</div>"""
        return _wrap_html(a.title, body)

    def _render_mermaid(self, a: CanvasArtifact) -> str:
        safe = _html.escape(a.content)
        body = f"""<pre class="mermaid" id="_mmd">{safe}</pre>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js" onerror="document.getElementById('_cdn_err').style.display=''"></script>
<script>if(typeof mermaid!=='undefined')mermaid.initialize({{startOnLoad:true}});</script>
<div id="_cdn_err" style="display:none;color:#c00;padding:8px;font-size:13px">⚠ Mermaid 渲染库加载失败（需要网络），已显示原始代码</div>"""
        return _wrap_html(a.title, body)

    def _render_chart(self, a: CanvasArtifact) -> str:
        import json
        safe_json = json.dumps(a.content)
        body = f"""<canvas id="chart"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js" onerror="document.getElementById('_cdn_err').style.display=''"></script>
<script>if(typeof Chart!=='undefined')new Chart(document.getElementById('chart'),JSON.parse({safe_json}));</script>
<div id="_cdn_err" style="display:none;color:#c00;padding:8px;font-size:13px">⚠ Chart.js 加载失败（需要网络），无法渲染图表</div>"""
        return _wrap_html(a.title, body)

    def _render_react(self, a: CanvasArtifact) -> str:
        body = f"""<div id="root"></div>
<script src="https://unpkg.com/react@18/umd/react.production.min.js" onerror="document.getElementById('_cdn_err').style.display=''"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
<script type="text/babel">{a.content}</script>
<div id="_cdn_err" style="display:none;color:#c00;padding:8px;font-size:13px">⚠ React 库加载失败（需要网络），无法渲染组件</div>"""
        return _wrap_html(a.title, body)

    def _notify(self, event: str, artifact: CanvasArtifact) -> None:
        for cb in self._listeners:
            try:
                cb(event, artifact)
            except Exception as e:
                logger.warning("Canvas listener error: %s", e)

def _wrap_html(title: str, body: str) -> str:
    safe_title = _html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title>
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
