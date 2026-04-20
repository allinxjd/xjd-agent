"""用户审批门 — Pipeline 关键节点暂停等待用户确认.

在 CLI 模式下直接 prompt 用户输入；
在 Gateway 模式下通过回调推送到前端，等待用户回复。

用法:
    # 注册回调 (Gateway 启动时)
    from agent.tools.approval_tools import set_approval_callback
    set_approval_callback(my_async_callback)

    # 工具调用 (LLM 自动触发)
    result = await request_user_approval(
        title="竞品分析结果",
        content="## 分析摘要\n...",
        options='[{"id":"A","label":"方向A: 简约白底"}]',
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)

_approval_callback: Optional[
    Callable[[str, str, list[dict], bool], Coroutine[Any, Any, str]]
] = None
_callback_lock = threading.Lock()

_TIMEOUT = 300.0


def set_approval_callback(
    callback: Callable[[str, str, list[dict], bool], Coroutine[Any, Any, str]],
) -> None:
    """注册 Gateway 模式的审批回调.

    callback(title, content, options, allow_custom) -> user_response
    """
    global _approval_callback
    with _callback_lock:
        _approval_callback = callback


def _parse_options(options_json: str) -> list[dict[str, str]]:
    if not options_json or not options_json.strip():
        return []
    try:
        parsed = json.loads(options_json)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Failed to parse approval options JSON: %s", e)
        pass
    return []


async def _cli_approval(
    title: str, content: str, options: list[dict], allow_custom: bool,
) -> str:
    """CLI 模式: Rich 输出 + 阻塞等待用户输入."""
    try:
        from rich.console import Console
        from rich.markdown import Markdown
        from rich.panel import Panel

        console = Console()
        console.print()
        console.print(Panel(Markdown(content), title=f"[bold cyan]{title}[/]", expand=False))

        if options:
            console.print()
            for opt in options:
                oid = opt.get("id", "?")
                label = opt.get("label", "")
                desc = opt.get("description", "")
                line = f"  [bold]{oid}[/] — {label}"
                if desc:
                    line += f"  [dim]({desc})[/dim]"
                console.print(line)

        if allow_custom:
            console.print("  [dim]或直接输入自定义内容[/dim]")

        console.print()
    except ImportError:
        print(f"\n{'='*60}")
        print(f"  {title}")
        print(f"{'='*60}")
        print(content)
        if options:
            for opt in options:
                print(f"  {opt.get('id','?')} — {opt.get('label','')}")

    if not sys.stdin.isatty():
        return "[ERROR] 非交互终端，无法获取用户输入"

    loop = asyncio.get_running_loop()
    try:
        response = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: input("请选择或输入: ")),
            timeout=_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return "[TIMEOUT] 用户未在 5 分钟内响应"

    return response.strip() or "[EMPTY]"


async def request_user_approval(
    title: str = "请确认",
    content: str = "",
    options: str = "",
    allow_custom: bool = True,
    **kwargs: Any,
) -> str:
    """向用户展示分析结果或设计方案，等待确认.

    Args:
        title: 展示标题
        content: Markdown 格式的展示内容
        options: 可选方案 JSON 数组 [{"id","label","description"}]
        allow_custom: 是否允许自定义输入

    Returns:
        用户选择的 option id 或自定义输入文本
    """
    parsed_options = _parse_options(options)

    with _callback_lock:
        cb = _approval_callback

    if cb is not None:
        try:
            response = await asyncio.wait_for(
                cb(title, content, parsed_options, allow_custom),
                timeout=_TIMEOUT,
            )
            return response
        except asyncio.TimeoutError:
            return "[TIMEOUT] 用户未在 5 分钟内响应"
        except Exception as e:
            logger.error("Approval callback failed, falling back to CLI", exc_info=True)

    return await _cli_approval(title, content, parsed_options, allow_custom)


def register_approval_tools(registry: Any) -> None:
    """注册审批工具到 ToolRegistry."""
    registry.register(
        name="request_user_approval",
        description=(
            "向用户展示分析结果或设计方案，等待用户确认、选择或修改。"
            "用于需要用户决策的关键节点，如竞品分析确认、设计方向选择、生成结果审核。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "展示标题，如'竞品分析结果'、'设计方案确认'",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown 格式的展示内容",
                },
                "options": {
                    "type": "string",
                    "description": '可选方案 JSON 数组，如 [{"id":"A","label":"方向A","description":"简约白底"}]',
                },
                "allow_custom": {
                    "type": "boolean",
                    "description": "是否允许用户自定义输入",
                    "default": True,
                },
            },
            "required": ["title", "content"],
        },
        handler=request_user_approval,
        category="core",
        timeout=_TIMEOUT,
    )
