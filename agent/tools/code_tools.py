"""代码相关工具集 — 代码执行 + 内容搜索 + Git + 补丁应用.

从 extended.py 提取的代码相关工具。
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Code Execution — 安全代码执行 (Python)
# ═══════════════════════════════════════════════════════════════════

async def execute_code(
    code: str,
    language: str = "python",
    timeout: int = 30,
) -> str:
    """安全执行代码.

    Args:
        code: 代码内容
        language: 编程语言 ("python" | "javascript" | "bash")
        timeout: 超时秒数

    Returns:
        执行输出 (stdout + stderr)
    """
    from agent.tools.builtin import _BLOCKED_PATTERNS
    if any(p.search(code) for p in _BLOCKED_PATTERNS):
        return "Error: 代码被安全策略拦截 — 禁止执行可能关闭浏览器或破坏系统的操作"

    if language == "python":
        cmd = ["python3", "-c", code]
    elif language == "javascript":
        cmd = ["node", "-e", code]
    elif language == "bash":
        cmd = ["bash", "-c", code]
    else:
        return f"Error: 不支持的语言 '{language}', 支持: python, javascript, bash"

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tempfile.gettempdir(),
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

        if len(output) > 20000:
            output = output[:10000] + "\n...(truncated)...\n" + output[-10000:]

        return output or "(no output)"

    except asyncio.TimeoutError:
        return f"Error: 代码执行超时 ({timeout}s)"
    except FileNotFoundError:
        return f"Error: {language} 运行时未找到"
    except Exception as e:
        return f"Error: {e}"

# ═══════════════════════════════════════════════════════════════════
#  Grep Search — 内容搜索 (ripgrep 风格)
# ═══════════════════════════════════════════════════════════════════

async def grep_search(
    pattern: str,
    path: str = ".",
    file_pattern: str = "",
    max_results: int = 50,
    ignore_case: bool = True,
) -> str:
    """在文件中搜索内容 (类似 grep/ripgrep).

    Args:
        pattern: 搜索模式 (正则表达式)
        path: 搜索目录或文件
        file_pattern: 文件名 glob 模式 (如 "*.py")
        max_results: 最大结果数
        ignore_case: 是否忽略大小写

    Returns:
        匹配的文件和行
    """
    p = Path(path).expanduser().resolve()
    if not p.exists():
        return f"Error: 路径不存在: {path}"

    try:
        flags = re.IGNORECASE if ignore_case else 0
        regex = re.compile(pattern, flags)
    except re.error as e:
        return f"Error: 无效正则: {e}"

    results = []
    files_searched = 0

    if p.is_file():
        files = [p]
    else:
        glob = file_pattern or "**/*"
        files = list(p.glob(glob))

    for f in files:
        if not f.is_file():
            continue
        if f.suffix in (".pyc", ".pyo", ".so", ".dylib", ".exe", ".dll",
                        ".jpg", ".png", ".gif", ".mp4", ".zip", ".tar", ".gz"):
            continue
        # 跳过隐藏目录和常见排除
        parts = f.relative_to(p).parts if p.is_dir() else ()
        if any(part.startswith(".") or part in ("node_modules", "__pycache__", "venv", ".venv") for part in parts):
            continue

        files_searched += 1
        try:
            with open(f, "r", encoding="utf-8", errors="replace") as fh:
                for line_num, line in enumerate(fh, 1):
                    if regex.search(line):
                        rel_path = f.relative_to(p) if p.is_dir() else f.name
                        results.append(f"  {rel_path}:{line_num}: {line.rstrip()[:200]}")
                        if len(results) >= max_results:
                            break
        except (OSError, UnicodeDecodeError) as exc:
            logger.debug("Skipping file %s: %s", f, exc)
            continue

        if len(results) >= max_results:
            break

    header = f"搜索 '{pattern}' (搜索了 {files_searched} 个文件, 找到 {len(results)} 个匹配)"
    if not results:
        return header + "\n  (无匹配)"

    if len(results) >= max_results:
        header += f" [截断, 仅显示前 {max_results} 个]"

    return header + "\n" + "\n".join(results)

# ═══════════════════════════════════════════════════════════════════
#  Apply Patch — 应用补丁
# ═══════════════════════════════════════════════════════════════════

async def _apply_patch(patch_content: str, target_dir: str = ".", strip: int = 1, **kwargs) -> str:
    """应用 unified diff 补丁."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "patch", f"-p{strip}", "--forward", "--no-backup-if-mismatch",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=target_dir,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(input=patch_content.encode()), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        if proc.returncode == 0:
            return f"补丁应用成功:\n{output}"
        else:
            err = stderr.decode("utf-8", errors="replace")
            return f"补丁应用失败 (exit {proc.returncode}):\n{output}\n{err}"
    except FileNotFoundError:
        return "错误: patch 命令未找到，请安装 patch 工具"
    except asyncio.TimeoutError:
        return "错误: 补丁应用超时"
    except Exception as e:
        return f"补丁应用失败: {e}"

# ═══════════════════════════════════════════════════════════════════
#  Git Command — Git 命令执行
# ═══════════════════════════════════════════════════════════════════

_GIT_SAFE = {"status", "log", "diff", "branch", "show", "remote", "tag", "stash", "blame", "shortlog"}
_GIT_WRITE = {"add", "commit", "push", "pull", "checkout", "merge", "rebase", "reset", "fetch"}

async def _git_command(subcommand: str, args: str = "", cwd: str = ".", **kwargs) -> str:
    """执行 git 命令."""
    import shlex
    sub = subcommand.strip().lower()
    if sub not in _GIT_SAFE and sub not in _GIT_WRITE:
        return f"不允许的 git 子命令: {sub}。允许: {', '.join(sorted(_GIT_SAFE | _GIT_WRITE))}"
    try:
        cmd = ["git", sub] + (shlex.split(args) if args else [])
    except ValueError as e:
        return f"参数解析失败: {e}"
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=cwd)
        output = r.stdout + r.stderr
        if len(output) > 20000:
            output = output[:20000] + "\n... (截断)"
        return output or "(无输出)"
    except Exception as e:
        return f"git 执行失败: {e}"


# ═══════════════════════════════════════════════════════════════════
#  注册代码相关工具
# ═══════════════════════════════════════════════════════════════════

def register_code_tools(registry: "ToolRegistry") -> None:
    """注册所有代码相关工具."""

    registry.register(
        name="execute_code",
        description="安全执行代码片段 (Python/JavaScript/Bash)。适用于计算、数据处理、快速验证。",
        parameters={
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的代码"},
                "language": {"type": "string", "description": "编程语言: python|javascript|bash", "default": "python"},
                "timeout": {"type": "integer", "description": "超时秒数 (默认 30)", "default": 30},
            },
            "required": ["code"],
        },
        handler=execute_code,
        category="code",
        requires_approval=True,
    )

    registry.register(
        name="grep_search",
        description="在文件中搜索内容 (支持正则表达式)。用于在代码库中查找函数、变量、配置等。",
        parameters={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "搜索模式 (正则表达式)"},
                "path": {"type": "string", "description": "搜索目录或文件", "default": "."},
                "file_pattern": {"type": "string", "description": "文件名 glob 模式 (如 *.py)", "default": ""},
                "max_results": {"type": "integer", "description": "最大结果数", "default": 50},
                "ignore_case": {"type": "boolean", "description": "忽略大小写", "default": True},
            },
            "required": ["pattern"],
        },
        handler=grep_search,
        category="file",
    )

    registry.register(
        name="git_command",
        description="执行 git 命令 (安全子命令: status, log, diff, branch, show, remote, tag)。",
        parameters={
            "type": "object",
            "properties": {
                "subcommand": {"type": "string", "description": "git 子命令"},
                "args": {"type": "string", "description": "额外参数", "default": ""},
                "cwd": {"type": "string", "description": "工作目录", "default": "."},
            },
            "required": ["subcommand"],
        },
        handler=_git_command,
        category="code",
    )

    registry.register(
        name="apply_patch",
        description="应用 unified diff 补丁到代码文件。",
        parameters={
            "type": "object",
            "properties": {
                "patch_content": {"type": "string", "description": "unified diff 格式的补丁内容"},
                "target_dir": {"type": "string", "description": "目标目录", "default": "."},
                "strip": {"type": "integer", "description": "strip 层级 (默认 1)", "default": 1},
            },
            "required": ["patch_content"],
        },
        handler=_apply_patch,
        category="code",
        requires_approval=True,
    )
