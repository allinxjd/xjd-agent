"""Canvas A2UI — 交互式可视化组件生成.

将 Agent 的输出渲染为交互式 UI 组件:
- HTML/CSS/JS 组件
- React 组件
- Markdown 增强 (表格、图表、代码高亮)
- 可编辑卡片 (用户可修改并提交)
- 表单生成
- 数据可视化 (Chart.js / ECharts)

"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

class ComponentType(str, Enum):
    """组件类型."""

    HTML = "html"                  # 原始 HTML
    REACT = "react"                # React JSX
    MARKDOWN = "markdown"          # 增强 Markdown
    FORM = "form"                  # 表单
    TABLE = "table"                # 数据表格
    CHART = "chart"                # 图表
    CODE = "code"                  # 代码编辑器
    CARD = "card"                  # 信息卡片
    KANBAN = "kanban"              # 看板
    TIMELINE = "timeline"          # 时间线
    TREE = "tree"                  # 树形结构
    DIFF = "diff"                  # 文件对比
    TERMINAL = "terminal"          # 终端输出
    MERMAID = "mermaid"            # Mermaid 图表

@dataclass
class CanvasComponent:
    """Canvas 组件."""

    id: str = ""
    type: ComponentType = ComponentType.HTML
    title: str = ""
    content: str = ""              # HTML/JSX/Markdown 内容
    data: dict[str, Any] = field(default_factory=dict)
    editable: bool = False
    interactive: bool = False
    width: str = "100%"
    height: str = "auto"
    theme: str = "light"           # "light" | "dark"
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"canvas_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "content": self.content,
            "data": self.data,
            "editable": self.editable,
            "interactive": self.interactive,
            "width": self.width,
            "height": self.height,
            "theme": self.theme,
        }

    def to_html(self) -> str:
        """渲染为 HTML."""
        if self.type == ComponentType.HTML:
            return self.content
        elif self.type == ComponentType.CODE:
            return self._render_code()
        elif self.type == ComponentType.TABLE:
            return self._render_table()
        elif self.type == ComponentType.CHART:
            return self._render_chart()
        elif self.type == ComponentType.FORM:
            return self._render_form()
        elif self.type == ComponentType.CARD:
            return self._render_card()
        elif self.type == ComponentType.MERMAID:
            return self._render_mermaid()
        elif self.type == ComponentType.TERMINAL:
            return self._render_terminal()
        elif self.type == ComponentType.DIFF:
            return self._render_diff()
        elif self.type == ComponentType.TIMELINE:
            return self._render_timeline()
        else:
            return f"<div>{self.content}</div>"

    def _render_code(self) -> str:
        lang = self.data.get("language", "")
        return f"""<div class="canvas-code" id="{self.id}">
<pre><code class="language-{lang}">{_escape_html(self.content)}</code></pre>
</div>"""

    def _render_table(self) -> str:
        headers = self.data.get("headers", [])
        rows = self.data.get("rows", [])

        header_html = "".join(f"<th>{_escape_html(str(h))}</th>" for h in headers)
        rows_html = ""
        for row in rows:
            cells = "".join(f"<td>{_escape_html(str(c))}</td>" for c in row)
            rows_html += f"<tr>{cells}</tr>"

        return f"""<div class="canvas-table" id="{self.id}">
<table>
<thead><tr>{header_html}</tr></thead>
<tbody>{rows_html}</tbody>
</table>
</div>"""

    def _render_chart(self) -> str:
        chart_type = self.data.get("chart_type", "bar")
        labels = json.dumps(self.data.get("labels", []))
        datasets = json.dumps(self.data.get("datasets", []))

        return f"""<div class="canvas-chart" id="{self.id}">
<canvas id="{self.id}_chart" width="600" height="400"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
new Chart(document.getElementById('{self.id}_chart'), {{
    type: '{chart_type}',
    data: {{ labels: {labels}, datasets: {datasets} }},
    options: {{ responsive: true }}
}});
</script>
</div>"""

    def _render_form(self) -> str:
        fields = self.data.get("fields", [])
        action = self.data.get("action", "#")

        fields_html = ""
        for f in fields:
            name = f.get("name", "")
            label = f.get("label", name)
            ftype = f.get("type", "text")
            required = "required" if f.get("required") else ""
            placeholder = f.get("placeholder", "")

            if ftype == "textarea":
                field_html = f'<textarea name="{name}" placeholder="{placeholder}" {required}></textarea>'
            elif ftype == "select":
                options = "".join(
                    f'<option value="{o}">{o}</option>'
                    for o in f.get("options", [])
                )
                field_html = f'<select name="{name}" {required}>{options}</select>'
            else:
                field_html = f'<input type="{ftype}" name="{name}" placeholder="{placeholder}" {required}>'

            fields_html += f'<div class="form-field"><label>{label}</label>{field_html}</div>'

        return f"""<div class="canvas-form" id="{self.id}">
<form action="{action}" method="post">
{fields_html}
<button type="submit">提交</button>
</form>
</div>"""

    def _render_card(self) -> str:
        title = self.data.get("title", self.title)
        subtitle = self.data.get("subtitle", "")
        image = self.data.get("image", "")
        actions = self.data.get("actions", [])

        img_html = f'<img src="{image}" />' if image else ""
        subtitle_html = f'<p class="subtitle">{_escape_html(subtitle)}</p>' if subtitle else ""
        actions_html = "".join(
            f'<button onclick="{a.get("onclick", "")}">{_escape_html(a.get("label", ""))}</button>'
            for a in actions
        )

        return f"""<div class="canvas-card" id="{self.id}">
{img_html}
<h3>{_escape_html(title)}</h3>
{subtitle_html}
<div class="content">{self.content}</div>
<div class="actions">{actions_html}</div>
</div>"""

    def _render_mermaid(self) -> str:
        return f"""<div class="canvas-mermaid" id="{self.id}">
<pre class="mermaid">{_escape_html(self.content)}</pre>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
<script>mermaid.initialize({{startOnLoad:true}});</script>
</div>"""

    def _render_terminal(self) -> str:
        return f"""<div class="canvas-terminal" id="{self.id}">
<div class="terminal-header">
<span class="dot red"></span><span class="dot yellow"></span><span class="dot green"></span>
<span class="title">{_escape_html(self.title or 'Terminal')}</span>
</div>
<pre class="terminal-body">{_escape_html(self.content)}</pre>
</div>"""

    def _render_diff(self) -> str:
        old = self.data.get("old", "")
        new = self.data.get("new", "")
        filename = self.data.get("filename", "")

        return f"""<div class="canvas-diff" id="{self.id}">
<div class="diff-header">{_escape_html(filename)}</div>
<div class="diff-old"><pre>{_escape_html(old)}</pre></div>
<div class="diff-new"><pre>{_escape_html(new)}</pre></div>
</div>"""

    def _render_timeline(self) -> str:
        events = self.data.get("events", [])
        items_html = ""
        for evt in events:
            items_html += f"""<div class="timeline-item">
<div class="time">{_escape_html(str(evt.get('time', '')))}</div>
<div class="event">{_escape_html(str(evt.get('title', '')))}</div>
<div class="detail">{_escape_html(str(evt.get('detail', '')))}</div>
</div>"""

        return f"""<div class="canvas-timeline" id="{self.id}">
{items_html}
</div>"""

def _escape_html(text: str) -> str:
    """HTML 转义."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )

class CanvasRenderer:
    """Canvas 渲染器 — 管理多个组件并输出.

    用法:
        renderer = CanvasRenderer()

        # 添加组件
        renderer.add(CanvasComponent(
            type=ComponentType.TABLE,
            title="用户列表",
            data={"headers": ["姓名", "邮箱"], "rows": [["张三", "z@e.com"]]},
        ))

        # 渲染完整 HTML 页面
        html = renderer.render_page()

        # 或者渲染单个组件
        component_html = renderer.render_component("canvas_xxx")
    """

    def __init__(self, theme: str = "light") -> None:
        self._components: dict[str, CanvasComponent] = {}
        self._theme = theme

    def add(self, component: CanvasComponent) -> str:
        """添加组件，返回 ID."""
        self._components[component.id] = component
        return component.id

    def remove(self, component_id: str) -> bool:
        """移除组件."""
        return bool(self._components.pop(component_id, None))

    def get(self, component_id: str) -> Optional[CanvasComponent]:
        return self._components.get(component_id)

    def list_components(self) -> list[CanvasComponent]:
        return list(self._components.values())

    def render_component(self, component_id: str) -> str:
        """渲染单个组件为 HTML."""
        comp = self._components.get(component_id)
        if not comp:
            return f"<!-- Component {component_id} not found -->"
        return comp.to_html()

    def render_all(self) -> str:
        """渲染所有组件为 HTML 片段."""
        parts = []
        for comp in self._components.values():
            parts.append(comp.to_html())
        return "\n".join(parts)

    def render_page(self, title: str = "XJD Canvas") -> str:
        """渲染为完整 HTML 页面."""
        body = self.render_all()
        return f"""<!DOCTYPE html>
<html lang="zh-CN" data-theme="{self._theme}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_escape_html(title)}</title>
<style>
{CANVAS_CSS}
</style>
</head>
<body>
<div class="canvas-container">
{body}
</div>
</body>
</html>"""

    def to_json(self) -> str:
        """序列化所有组件为 JSON."""
        return json.dumps(
            [c.to_dict() for c in self._components.values()],
            ensure_ascii=False,
            indent=2,
        )

# 自动检测 Agent 输出中的可视化内容
class CanvasAutoDetector:
    """自动检测 Agent 输出中可以转为 Canvas 的内容.

    例如:
    - Markdown 表格 → ComponentType.TABLE
    - 代码块 → ComponentType.CODE
    - Mermaid → ComponentType.MERMAID
    - JSON 数组 → ComponentType.TABLE
    """

    def detect(self, text: str) -> list[CanvasComponent]:
        """检测并生成组件."""
        components = []

        # 检测 Markdown 表格
        table_pattern = r'\|(.+)\|\n\|[-\s|]+\|\n((?:\|.+\|\n?)+)'
        for m in re.finditer(table_pattern, text):
            headers = [h.strip() for h in m.group(1).split("|") if h.strip()]
            rows = []
            for row_line in m.group(2).strip().split("\n"):
                cells = [c.strip() for c in row_line.split("|") if c.strip()]
                if cells:
                    rows.append(cells)
            if headers and rows:
                components.append(CanvasComponent(
                    type=ComponentType.TABLE,
                    data={"headers": headers, "rows": rows},
                ))

        # 检测代码块
        code_pattern = r'```(\w+)?\n(.*?)```'
        for m in re.finditer(code_pattern, text, re.DOTALL):
            lang = m.group(1) or ""
            code = m.group(2).strip()

            if lang == "mermaid":
                components.append(CanvasComponent(
                    type=ComponentType.MERMAID,
                    content=code,
                ))
            else:
                components.append(CanvasComponent(
                    type=ComponentType.CODE,
                    content=code,
                    data={"language": lang},
                ))

        # 检测 JSON 数据
        json_pattern = r'```json\n(\[[\s\S]*?\])\n```'
        for m in re.finditer(json_pattern, text):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    headers = list(data[0].keys())
                    rows = [[str(item.get(h, "")) for h in headers] for item in data]
                    components.append(CanvasComponent(
                        type=ComponentType.TABLE,
                        title="Data",
                        data={"headers": headers, "rows": rows},
                    ))
            except json.JSONDecodeError:
                pass

        return components

# ═══════════════════════════════════════════════════════════════════
#  内置 CSS 样式
# ═══════════════════════════════════════════════════════════════════

CANVAS_CSS = """
:root {
    --bg: #ffffff;
    --fg: #1a1a2e;
    --border: #e2e8f0;
    --primary: #4f46e5;
    --primary-light: #eef2ff;
    --success: #10b981;
    --warning: #f59e0b;
    --danger: #ef4444;
    --radius: 8px;
    --shadow: 0 1px 3px rgba(0,0,0,0.1);
}

[data-theme="dark"] {
    --bg: #1a1a2e;
    --fg: #e2e8f0;
    --border: #334155;
    --primary-light: #312e81;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--fg);
    margin: 0;
    padding: 16px;
}

.canvas-container {
    max-width: 1200px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 16px;
}

.canvas-table table {
    width: 100%;
    border-collapse: collapse;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
}

.canvas-table th,
.canvas-table td {
    padding: 10px 14px;
    text-align: left;
    border-bottom: 1px solid var(--border);
}

.canvas-table th {
    background: var(--primary-light);
    font-weight: 600;
}

.canvas-code pre {
    background: #1e1e2e;
    color: #cdd6f4;
    padding: 16px;
    border-radius: var(--radius);
    overflow-x: auto;
    font-family: "Fira Code", "JetBrains Mono", monospace;
    font-size: 14px;
    line-height: 1.6;
}

.canvas-card {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 20px;
    box-shadow: var(--shadow);
}

.canvas-card h3 { margin: 0 0 8px 0; }
.canvas-card .subtitle { color: #64748b; margin: 0 0 12px 0; }
.canvas-card .actions { display: flex; gap: 8px; margin-top: 16px; }
.canvas-card .actions button {
    padding: 6px 16px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    cursor: pointer;
    background: var(--primary);
    color: white;
}

.canvas-terminal {
    border-radius: var(--radius);
    overflow: hidden;
    border: 1px solid var(--border);
}

.canvas-terminal .terminal-header {
    background: #e2e8f0;
    padding: 8px 12px;
    display: flex;
    align-items: center;
    gap: 6px;
}

.canvas-terminal .dot { width: 12px; height: 12px; border-radius: 50%; }
.canvas-terminal .dot.red { background: #ef4444; }
.canvas-terminal .dot.yellow { background: #f59e0b; }
.canvas-terminal .dot.green { background: #10b981; }
.canvas-terminal .title { margin-left: 8px; font-size: 13px; color: #64748b; }

.canvas-terminal .terminal-body {
    background: #1e1e2e;
    color: #a6e3a1;
    padding: 16px;
    font-family: monospace;
    font-size: 14px;
    line-height: 1.5;
    margin: 0;
}

.canvas-form { padding: 20px; }
.canvas-form .form-field { margin-bottom: 16px; }
.canvas-form label { display: block; margin-bottom: 4px; font-weight: 500; }
.canvas-form input, .canvas-form textarea, .canvas-form select {
    width: 100%;
    padding: 8px 12px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-size: 14px;
    box-sizing: border-box;
}
.canvas-form button[type="submit"] {
    background: var(--primary);
    color: white;
    border: none;
    padding: 10px 24px;
    border-radius: var(--radius);
    cursor: pointer;
    font-size: 14px;
}

.canvas-timeline .timeline-item {
    padding: 12px 0 12px 24px;
    border-left: 2px solid var(--primary);
    position: relative;
}
.canvas-timeline .timeline-item::before {
    content: '';
    width: 10px;
    height: 10px;
    background: var(--primary);
    border-radius: 50%;
    position: absolute;
    left: -6px;
    top: 16px;
}
.canvas-timeline .time { font-size: 12px; color: #64748b; }
.canvas-timeline .event { font-weight: 600; }
.canvas-timeline .detail { font-size: 14px; color: #64748b; margin-top: 4px; }

.canvas-diff .diff-header {
    background: var(--primary-light);
    padding: 8px 12px;
    font-family: monospace;
    border-radius: var(--radius) var(--radius) 0 0;
}
.canvas-diff .diff-old pre,
.canvas-diff .diff-new pre {
    padding: 12px;
    margin: 0;
    font-family: monospace;
    font-size: 13px;
}
.canvas-diff .diff-old pre { background: #fef2f2; color: #dc2626; }
.canvas-diff .diff-new pre { background: #f0fdf4; color: #16a34a; }
"""
