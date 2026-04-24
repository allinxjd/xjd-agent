"""杂项工具 — calendar_event, skill_audit, list_terminals.

这些工具不属于任何大类，统一放在 misc_tools.py。
"""

from __future__ import annotations

import json
import logging
import tempfile
from agent.core.workspace_files import workspace_tmp
from pathlib import Path

logger = logging.getLogger(__name__)


async def _skill_audit(skill_name: str = "", **kwargs) -> str:
    """审计技能安全性 — 检查技能步骤中的危险操作."""
    from agent.core.config import get_skills_dir
    import yaml

    skills_dir = get_skills_dir()
    dangerous_patterns = ["rm -rf", "sudo", "chmod 777", "curl | bash", "eval(", "exec(", "DROP TABLE", "DELETE FROM"]

    if skill_name:
        files = [skills_dir / f"{skill_name}.yaml"]
    else:
        files = list(skills_dir.glob("*.yaml"))

    if not files:
        return "未找到技能文件"

    results = []
    for f in files:
        if not f.exists():
            continue
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            warnings = []
            steps = data.get("steps", [])
            for i, step in enumerate(steps):
                step_str = json.dumps(step, ensure_ascii=False) if isinstance(step, dict) else str(step)
                for pat in dangerous_patterns:
                    if pat.lower() in step_str.lower():
                        warnings.append(f"  步骤 {i+1}: 包含危险操作 '{pat}'")
            status = f"⚠️ {len(warnings)} 个警告" if warnings else "✅ 安全"
            results.append(f"{f.stem}: {status}")
            results.extend(warnings)
        except Exception as e:
            results.append(f"{f.stem}: 解析失败 ({e})")

    return "技能安全审计:\n" + "\n".join(results)


async def _calendar_event(
    action: str, title: str = "", start: str = "", end: str = "", path: str = "", **kwargs
) -> str:
    """日历事件."""
    if action == "create":
        if not title or not start:
            return "错误: create 需要 title 和 start 参数"
        from datetime import datetime
        ics = (
            "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\n"
            f"SUMMARY:{title}\n"
            f"DTSTART:{start.replace('-', '').replace(':', '').replace(' ', 'T')}\n"
        )
        if end:
            ics += f"DTEND:{end.replace('-', '').replace(':', '').replace(' ', 'T')}\n"
        ics += "END:VEVENT\nEND:VCALENDAR"
        out = str(workspace_tmp(".ics", "event_"))
        Path(out).write_text(ics)
        return f"日历事件已创建: {out}"
    elif action == "parse":
        if not path:
            return "错误: parse 需要 path 参数"
        content = Path(path).expanduser().read_text()
        return content[:10000]
    return f"未知操作: {action}"


def register_misc_tools(registry) -> None:
    """注册杂项工具."""

    registry.register(
        name="calendar_event",
        description="创建或解析 iCalendar (.ics) 事件。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作: create 或 parse", "enum": ["create", "parse"]},
                "title": {"type": "string", "description": "事件标题 (create)"},
                "start": {"type": "string", "description": "开始时间 ISO 格式 (create)"},
                "end": {"type": "string", "description": "结束时间 ISO 格式 (create)"},
                "path": {"type": "string", "description": ".ics 文件路径 (parse)"},
            },
            "required": ["action"],
        },
        handler=_calendar_event,
        category="productivity",
    )

    registry.register(
        name="skill_audit",
        description="审计技能安全性，检查步骤中的危险操作。",
        parameters={
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "技能名称 (留空审计全部)", "default": ""},
            },
            "required": [],
        },
        handler=_skill_audit,
        category="system",
    )

    # ── 终端后端管理 ──
    try:
        from agent.core.terminal import TerminalManager

        _terminal_mgr = TerminalManager()

        async def _list_terminals(**kw) -> str:
            backends = _terminal_mgr.list_backends()
            default = _terminal_mgr.default_backend
            lines = [f"{'* ' if b == default else '  '}{b}" for b in backends]
            return "终端后端:\n" + "\n".join(lines)

        registry.register(
            name="list_terminals",
            description="列出可用的终端后端。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_list_terminals,
            category="terminal",
        )
    except Exception as e:
        logger.debug("Terminal tools not available: %s", e)
