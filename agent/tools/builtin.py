"""终端执行工具 + 文件操作工具.

"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

async def run_terminal(
    command: str,
    workdir: Optional[str] = None,
    timeout: int = 120,
) -> str:
    """执行终端命令.

    Args:
        command: 要执行的命令
        workdir: 工作目录
        timeout: 超时 (秒)

    Returns:
        命令输出 (stdout + stderr)
    """
    cwd = workdir or os.getcwd()

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += stderr.decode("utf-8", errors="replace")

        exit_code = proc.returncode
        if exit_code != 0:
            output = f"[Exit code: {exit_code}]\n{output}"

        # 截断过长输出
        if len(output) > 50000:
            output = output[:25000] + "\n...(truncated)...\n" + output[-25000:]

        return output or "(no output)"

    except asyncio.TimeoutError:
        return f"Error: Command timed out after {timeout}s"
    except Exception as e:
        return f"Error: {e}"

async def read_file(
    path: str,
    offset: int = 0,
    limit: int = 2000,
) -> str:
    """读取文件内容.

    Args:
        path: 文件路径
        offset: 起始行号 (0-based)
        limit: 最大行数

    Returns:
        文件内容 (带行号)
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {path}"
        if not p.is_file():
            return f"Error: Not a file: {path}"

        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total_lines = len(lines)
        selected = lines[offset: offset + limit]

        result_lines = []
        for i, line in enumerate(selected, start=offset + 1):
            result_lines.append(f"{i:>6}\t{line.rstrip()}")

        header = f"[{p.name}] Lines {offset + 1}-{offset + len(selected)} of {total_lines}"
        return header + "\n" + "\n".join(result_lines)

    except Exception as e:
        return f"Error reading file: {e}"

async def write_file(
    path: str,
    content: str,
) -> str:
    """写入文件 (覆盖).

    Args:
        path: 文件路径
        content: 文件内容

    Returns:
        操作结果
    """
    try:
        p = Path(path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        with open(p, "w", encoding="utf-8") as f:
            f.write(content)

        return f"Written {len(content)} chars to {p}"

    except Exception as e:
        return f"Error writing file: {e}"

async def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """编辑文件 (精确替换).

    Args:
        path: 文件路径
        old_string: 要替换的文本
        new_string: 替换为的文本
        replace_all: 是否替换所有匹配

    Returns:
        操作结果
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: File not found: {path}"

        with open(p, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1 and not replace_all:
            return f"Error: old_string found {count} times. Use replace_all=true or provide more context."

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        with open(p, "w", encoding="utf-8") as f:
            f.write(new_content)

        return f"Replaced {'all ' + str(count) + ' occurrences' if replace_all else '1 occurrence'} in {p}"

    except Exception as e:
        return f"Error editing file: {e}"

async def list_directory(
    path: str = ".",
    pattern: str = "*",
) -> str:
    """列出目录内容.

    Args:
        path: 目录路径
        pattern: glob 模式

    Returns:
        文件列表
    """
    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"Error: Directory not found: {path}"
        if not p.is_dir():
            return f"Error: Not a directory: {path}"

        items = sorted(p.glob(pattern))
        result = []
        for item in items[:200]:  # 限制数量
            icon = "📁" if item.is_dir() else "📄"
            size = ""
            if item.is_file():
                s = item.stat().st_size
                if s < 1024:
                    size = f" ({s}B)"
                elif s < 1024 * 1024:
                    size = f" ({s // 1024}KB)"
                else:
                    size = f" ({s // (1024 * 1024)}MB)"
            result.append(f"  {icon} {item.name}{size}")

        header = f"[{p}] {len(items)} items"
        if len(items) > 200:
            header += " (showing first 200)"
        return header + "\n" + "\n".join(result)

    except Exception as e:
        return f"Error listing directory: {e}"

def register_builtin_tools(registry: "ToolRegistry") -> None:
    """注册所有内置工具到注册表."""
    from agent.tools.registry import ToolRegistry

    registry.register(
        name="run_terminal",
        description="在终端执行命令。用于运行 shell 命令、脚本、查看系统信息等。",
        parameters={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                },
                "workdir": {
                    "type": "string",
                    "description": "工作目录 (可选)",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时秒数 (默认 120)",
                    "default": 120,
                },
            },
            "required": ["command"],
        },
        handler=run_terminal,
        category="terminal",
        requires_approval=False,
    )

    registry.register(
        name="read_file",
        description="读取文件内容，返回带行号的文本。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径"},
                "offset": {"type": "integer", "description": "起始行号 (默认 0)", "default": 0},
                "limit": {"type": "integer", "description": "最大行数 (默认 2000)", "default": 2000},
            },
            "required": ["path"],
        },
        handler=read_file,
        category="file",
    )

    registry.register(
        name="write_file",
        description="创建或覆盖写入文件。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
        category="file",
    )

    registry.register(
        name="edit_file",
        description="精确替换文件中的文本片段。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件绝对路径"},
                "old_string": {"type": "string", "description": "要替换的原始文本"},
                "new_string": {"type": "string", "description": "替换后的文本"},
                "replace_all": {"type": "boolean", "description": "是否替换所有匹配", "default": False},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=edit_file,
        category="file",
    )

    registry.register(
        name="list_directory",
        description="列出目录下的文件和子目录。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径", "default": "."},
                "pattern": {"type": "string", "description": "glob 匹配模式", "default": "*"},
            },
        },
        handler=list_directory,
        category="file",
    )
