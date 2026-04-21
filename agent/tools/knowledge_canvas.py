"""知识画布 — 记忆/学习/技能可视化.

将 Agent 的内部知识状态渲染为交互式 Canvas:
- 记忆网络图 (Mermaid)
- 学习曲线 (Chart.js)
- 技能树 (HTML)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_memory_manager = None
_learning_loop = None


def _set_providers(memory_mgr=None, learning=None) -> None:
    global _memory_manager, _learning_loop
    if memory_mgr:
        _memory_manager = memory_mgr
    if learning:
        _learning_loop = learning


async def _show_knowledge_canvas(view: str = "overview", **kw) -> str:
    from agent.tools.canvas_tools import _canvas_mgr, _create_canvas

    if view == "memory_graph":
        content = await _build_memory_graph()
    elif view == "learning_curve":
        content = await _build_learning_curve()
    elif view == "skill_tree":
        content = await _build_skill_tree()
    else:
        content = await _build_overview()

    return await _create_canvas(
        type="html", title="知识画布", content=content
    )


async def _build_overview() -> str:
    sections = []

    # 记忆统计
    mem_stats = await _get_memory_stats()
    sections.append(f"""
    <div class="kc-section">
      <h2>记忆系统</h2>
      <div class="kc-grid">
        {"".join(f'<div class="kc-card"><div class="kc-num">{v}</div><div class="kc-label">{k}</div></div>' for k, v in mem_stats.items())}
      </div>
    </div>""")

    # 学习统计
    learn_stats = _get_learning_stats()
    if learn_stats:
        rate = f"{learn_stats.get('success_rate', 0):.0%}"
        sections.append(f"""
    <div class="kc-section">
      <h2>学习进度</h2>
      <div class="kc-grid">
        <div class="kc-card"><div class="kc-num">{learn_stats.get('total_turns', 0)}</div><div class="kc-label">总轮次</div></div>
        <div class="kc-card"><div class="kc-num">{rate}</div><div class="kc-label">成功率</div></div>
        <div class="kc-card"><div class="kc-num">{learn_stats.get('skills_created', 0)}</div><div class="kc-label">技能习得</div></div>
        <div class="kc-card"><div class="kc-num">{learn_stats.get('memories_extracted', 0)}</div><div class="kc-label">记忆提取</div></div>
      </div>
    </div>""")

    return _KC_STYLE + "\n".join(sections)


async def _build_memory_graph() -> str:
    if not _memory_manager:
        return "<p>记忆系统未初始化</p>"

    from agent.memory.provider import MemoryType
    type_counts = {}
    for mt in MemoryType:
        try:
            memories = await _memory_manager.list_memories(memory_type=mt)
            type_counts[mt.value] = len(memories) if memories else 0
        except Exception:
            type_counts[mt.value] = 0

    nodes = []
    for mt, count in type_counts.items():
        if count > 0:
            nodes.append(f'    {mt}["{mt}\\n({count})"]')

    links = []
    pairs = [("fact", "context"), ("skill", "procedural"), ("episodic", "meta"), ("preference", "fact"), ("context", "relationship")]
    for a, b in pairs:
        if type_counts.get(a, 0) > 0 and type_counts.get(b, 0) > 0:
            links.append(f"    {a} --- {b}")

    mermaid = "graph TD\n" + "\n".join(nodes) + "\n" + "\n".join(links)
    return f"""<pre class="mermaid">{mermaid}</pre>
<script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js" onerror="document.getElementById('_cdn_err').style.display=''"></script>
<script>if(typeof mermaid!=='undefined')mermaid.initialize({{startOnLoad:true,theme:'neutral'}});</script>
<div id="_cdn_err" style="display:none;color:#c00;padding:8px;font-size:13px">⚠ Mermaid 加载失败（需要网络），已显示原始代码</div>"""


async def _build_learning_curve() -> str:
    stats = _get_learning_stats()
    if not stats:
        return "<p>学习系统未初始化</p>"

    total = stats.get("total_turns", 0)
    success = stats.get("successful_turns", 0)
    failed = stats.get("failed_turns", 0)
    skills = stats.get("skills_created", 0)

    chart_config = json.dumps({
        "type": "bar",
        "data": {
            "labels": ["成功", "失败", "技能习得", "技能优化", "记忆提取"],
            "datasets": [{
                "label": "学习统计",
                "data": [success, failed, skills, stats.get("skills_refined", 0), stats.get("memories_extracted", 0)],
                "backgroundColor": ["#4caf50", "#f44336", "#2196f3", "#ff9800", "#9c27b0"],
            }]
        },
        "options": {"responsive": True, "plugins": {"title": {"display": True, "text": f"学习曲线 (共 {total} 轮)"}}}
    })
    return f"""<canvas id="lc"></canvas>
<script src="https://cdn.jsdelivr.net/npm/chart.js" onerror="document.getElementById('_cdn_err').style.display=''"></script>
<script>if(typeof Chart!=='undefined')new Chart(document.getElementById('lc'),{chart_config});</script>
<div id="_cdn_err" style="display:none;color:#c00;padding:8px;font-size:13px">⚠ Chart.js 加载失败（需要网络），无法渲染图表</div>"""


async def _build_skill_tree() -> str:
    if not _memory_manager:
        return "<p>记忆系统未初始化</p>"

    try:
        from agent.memory.provider import MemoryType
        skills = await _memory_manager.list_memories(memory_type=MemoryType.SKILL)
    except Exception:
        skills = []

    if not skills:
        return _KC_STYLE + '<div class="kc-section"><h2>技能树</h2><p>暂无已习得技能</p></div>'

    items = []
    for s in skills[:30]:
        score = getattr(s, "usefulness_score", 0.5)
        bar_w = int(score * 100)
        items.append(f"""<div class="kc-skill">
  <div class="kc-skill-name">{_esc(s.content[:80])}</div>
  <div class="kc-bar"><div class="kc-bar-fill" style="width:{bar_w}%"></div></div>
  <span class="kc-score">{score:.0%}</span>
</div>""")

    return _KC_STYLE + f'<div class="kc-section"><h2>技能树 ({len(skills)} 项)</h2>{"".join(items)}</div>'


async def _get_memory_stats() -> dict[str, int]:
    if not _memory_manager:
        return {"未初始化": 0}
    from agent.memory.provider import MemoryType
    stats = {}
    for mt in MemoryType:
        try:
            memories = await _memory_manager.list_memories(memory_type=mt)
            stats[mt.value] = len(memories) if memories else 0
        except Exception:
            stats[mt.value] = 0
    return stats


def _get_learning_stats() -> Optional[dict]:
    if not _learning_loop:
        return None
    try:
        s = _learning_loop._stats
        total = s.total_turns or 1
        return {
            "total_turns": s.total_turns,
            "successful_turns": s.successful_turns,
            "failed_turns": s.failed_turns,
            "success_rate": s.successful_turns / total,
            "skills_created": s.skills_created,
            "skills_refined": s.skills_refined,
            "memories_extracted": s.memories_extracted,
        }
    except Exception:
        return None


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_KC_STYLE = """<style>
.kc-section{margin:1.5rem 0}
.kc-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:1rem;margin:1rem 0}
.kc-card{background:#f8f9fa;border-radius:12px;padding:1.2rem;text-align:center}
.kc-num{font-size:2rem;font-weight:700;color:#1a73e8}
.kc-label{font-size:.85rem;color:#666;margin-top:.3rem}
.kc-skill{display:flex;align-items:center;gap:.8rem;padding:.6rem 0;border-bottom:1px solid #eee}
.kc-skill-name{flex:1;font-size:.9rem}
.kc-bar{width:120px;height:8px;background:#e0e0e0;border-radius:4px;overflow:hidden}
.kc-bar-fill{height:100%;background:linear-gradient(90deg,#4caf50,#8bc34a);border-radius:4px}
.kc-score{font-size:.8rem;color:#888;width:3rem;text-align:right}
</style>"""


def register_knowledge_canvas_tools(registry, memory_manager=None, learning_loop=None) -> None:
    _set_providers(memory_manager, learning_loop)
    try:
        registry.register(
            name="show_knowledge_canvas",
            description="展示知识画布 — 可视化记忆网络、学习曲线、技能树。",
            parameters={
                "type": "object",
                "properties": {
                    "view": {
                        "type": "string",
                        "description": "视图类型",
                        "enum": ["overview", "memory_graph", "learning_curve", "skill_tree"],
                    },
                },
            },
            handler=_show_knowledge_canvas,
            category="canvas",
        )
    except Exception as e:
        logger.debug("Knowledge canvas tools not available: %s", e)