"""系统相关工具 — 系统信息、进程管理、环境变量、剪贴板、Cron 任务.

从 extended.py 提取的系统类工具集合。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import signal
import subprocess

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Handler Functions
# ═══════════════════════════════════════════════════════════════════

async def _system_info(**kwargs) -> str:
    """获取系统信息."""
    import os
    uname = platform.uname()
    disk = shutil.disk_usage("/")
    try:
        load = os.getloadavg()
        load_str = f"  负载: {load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}"
    except (OSError, AttributeError):
        load_str = ""
    return (
        f"系统: {uname.system} {uname.release} ({uname.machine})\n"
        f"主机: {uname.node}\n"
        f"Python: {platform.python_version()}\n"
        f"CPU: {os.cpu_count()} 核\n"
        f"磁盘: {disk.used // (1 << 30)}GB / {disk.total // (1 << 30)}GB "
        f"(空闲 {disk.free // (1 << 30)}GB)\n"
        f"{load_str}"
    )

async def _process_manager(action: str, pid: int = 0, filter: str = "", **kwargs) -> str:
    """进程管理."""
    if action == "list":
        try:
            r = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
            lines = r.stdout.strip().split("\n")
            if filter:
                lines = [lines[0]] + [l for l in lines[1:] if filter.lower() in l.lower()]
            output = "\n".join(lines[:100])
            return output
        except Exception as e:
            return f"列出进程失败: {e}"
    elif action == "kill":
        if not pid:
            return "错误: kill 需要 pid 参数"
        try:
            os.kill(pid, signal.SIGTERM)
            return f"已发送 SIGTERM 到进程 {pid}"
        except Exception as e:
            return f"终止进程失败: {e}"
    return f"未知操作: {action}"

async def _env_variable(action: str, name: str = "", value: str = "", **kw) -> str:
    """环境变量操作."""
    if action == "get":
        v = os.environ.get(name, "")
        return v if v else f"环境变量 {name} 未设置"
    elif action == "set":
        os.environ[name] = value
        return f"已设置 {name}={value}"
    elif action == "list":
        items = sorted(os.environ.items())[:50]
        return "\n".join(f"{k}={v[:80]}" for k, v in items)
    return f"不支持的操作: {action}"

async def _clipboard_read(**kwargs) -> str:
    """读取剪贴板."""
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        else:
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"], capture_output=True, text=True, timeout=5)
        return r.stdout or "(剪贴板为空)"
    except Exception as e:
        return f"读取剪贴板失败: {e}"

async def _clipboard_write(content: str, **kwargs) -> str:
    """写入剪贴板."""
    try:
        if platform.system() == "Darwin":
            subprocess.run(["pbcopy"], input=content, text=True, timeout=5)
        else:
            subprocess.run(["xclip", "-selection", "clipboard"], input=content, text=True, timeout=5)
        return f"已写入剪贴板 ({len(content)} 字符)"
    except Exception as e:
        return f"写入剪贴板失败: {e}"

async def _cron_schedule(action: str, expression: str = "", command: str = "", **kw) -> str:
    """Cron 任务管理."""
    try:
        if action == "list":
            proc = await asyncio.create_subprocess_exec(
                "crontab", "-l",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode() or "无 cron 任务"
        elif action == "create":
            if not expression or not command:
                return "需要 expression 和 command"
            from croniter import croniter
            if not croniter.is_valid(expression):
                return f"无效的 cron 表达式: {expression}"
            proc = await asyncio.create_subprocess_exec(
                "crontab", "-l",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            current = stdout.decode().rstrip("\n")
            new_line = f"{expression} {command}"
            new_crontab = f"{current}\n{new_line}\n" if current else f"{new_line}\n"
            proc2 = await asyncio.create_subprocess_exec(
                "crontab", "-",
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc2.communicate(input=new_crontab.encode())
            return f"已添加: {new_line}"
        elif action == "delete":
            return "请手动编辑 crontab (crontab -e) 删除任务"
        return f"不支持的操作: {action}"
    except Exception as e:
        return f"Cron 操作失败: {e}"


# ═══════════════════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════════════════

def register_system_tools(registry):
    """注册所有系统相关工具."""

    registry.register(
        name="system_info",
        description="获取系统信息: 操作系统、CPU、内存、磁盘使用情况。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_system_info,
        category="system",
    )

    registry.register(
        name="process_manager",
        description="查看或管理系统进程。action: list (列出进程) 或 kill (终止进程)。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作: list 或 kill", "enum": ["list", "kill"]},
                "pid": {"type": "integer", "description": "进程 PID (kill 时必填)"},
                "filter": {"type": "string", "description": "过滤关键词 (list 时可选)"},
            },
            "required": ["action"],
        },
        handler=_process_manager,
        category="system",
        requires_approval=True,
    )

    registry.register(
        name="env_variable",
        description="获取/设置/列出环境变量。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作", "enum": ["get", "set", "list"]},
                "name": {"type": "string", "description": "变量名"},
                "value": {"type": "string", "description": "变量值 (set 时)"},
            },
            "required": ["action"],
        },
        handler=_env_variable,
        category="system",
        requires_approval=True,
    )

    registry.register(
        name="clipboard_read",
        description="读取系统剪贴板内容。",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_clipboard_read,
        category="system",
    )

    registry.register(
        name="clipboard_write",
        description="写入内容到系统剪贴板。",
        parameters={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["content"],
        },
        handler=_clipboard_write,
        category="system",
    )

    registry.register(
        name="cron_schedule",
        description="管理系统 cron 定时任务。",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "操作", "enum": ["list", "create", "delete"]},
                "expression": {"type": "string", "description": "Cron 表达式 (create 时)"},
                "command": {"type": "string", "description": "要执行的命令 (create 时)"},
            },
            "required": ["action"],
        },
        handler=_cron_schedule,
        category="system",
        requires_approval=True,
    )
