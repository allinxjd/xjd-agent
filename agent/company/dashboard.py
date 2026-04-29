"""Company Dashboard — WebUI API 路由."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_company_routes(app: Any) -> None:
    """注册 Company 仪表盘路由到 aiohttp app."""
    from aiohttp import web

    app.router.add_get("/company", _serve_dashboard)
    app.router.add_get("/api/company/team", _api_team)
    app.router.add_get("/api/company/tasks", _api_tasks)
    app.router.add_get("/api/company/messages", _api_messages)
    app.router.add_get("/api/company/runs", _api_runs)
    app.router.add_get("/api/company/status", _api_status)


async def _serve_dashboard(request: Any) -> Any:
    from aiohttp import web
    import pathlib
    html_path = pathlib.Path(__file__).parent.parent.parent / "web" / "static" / "company-dashboard.html"
    if html_path.exists():
        return web.FileResponse(html_path)
    return web.Response(text="Company Dashboard HTML not found", status=404)


async def _api_team(request: Any) -> Any:
    from aiohttp import web
    from agent.company.roles import create_default_team
    from agent.company.yaml_loader import load_custom_roles

    team = create_default_team()
    custom = load_custom_roles()
    all_roles = team + custom

    data = [
        {
            "name": r.name,
            "description": r.description,
            "goal": r.goal,
            "watch_actions": r.watch_actions,
            "actions": [a.name for a in r.actions],
            "tools_filter": r.tools_filter,
            "react_mode": r.react_mode,
            "is_custom": r in custom,
        }
        for r in all_roles
    ]
    return web.json_response(data)


async def _api_tasks(request: Any) -> Any:
    from aiohttp import web
    from agent.company.store import CompanyStore

    status = request.query.get("status", "")
    store = CompanyStore()
    store.open()
    try:
        tasks = store.list_tasks(status=status or None)
        data = [
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": t.status,
                "assigned_to": t.assigned_to,
                "retry_count": t.retry_count,
                "result_preview": t.result[:200] if t.result else "",
            }
            for t in tasks
        ]
        return web.json_response(data)
    finally:
        store.close()


async def _api_messages(request: Any) -> Any:
    from aiohttp import web
    from agent.company.store import CompanyStore

    task_id = request.query.get("task_id", "")
    limit = int(request.query.get("limit", "50"))
    store = CompanyStore()
    store.open()
    try:
        messages = store.list_messages(task_id=task_id, limit=limit)
        data = [
            {
                "msg_id": m.msg_id,
                "cause_by": m.cause_by,
                "sent_from": m.sent_from,
                "send_to": m.send_to,
                "content_preview": m.content[:300],
                "timestamp": m.timestamp,
            }
            for m in messages
        ]
        return web.json_response(data)
    finally:
        store.close()


async def _api_runs(request: Any) -> Any:
    from aiohttp import web
    from agent.company.store import CompanyStore

    store = CompanyStore()
    store.open()
    try:
        runs = store.list_runs(limit=20)
        return web.json_response(runs)
    finally:
        store.close()


async def _api_status(request: Any) -> Any:
    from aiohttp import web
    from agent.company.company import _load_karpathy_guidelines
    from agent.company.roles import create_default_team
    from agent.company.store import CompanyStore
    from agent.company.yaml_loader import load_custom_roles, list_workflows

    guidelines = _load_karpathy_guidelines()
    team = create_default_team()
    custom = load_custom_roles()
    workflows = list_workflows()

    store = CompanyStore()
    store.open()
    try:
        resumable = store.get_resumable_run()
        recent_runs = store.list_runs(limit=5)
    finally:
        store.close()

    return web.json_response({
        "karpathy_loaded": bool(guidelines),
        "karpathy_chars": len(guidelines),
        "builtin_roles": len(team),
        "custom_roles": len(custom),
        "workflows": len(workflows),
        "resumable_run": resumable,
        "recent_runs": recent_runs,
    })
