"""本地开发环境探测 — 轻量、快速、不安装任何东西."""

from __future__ import annotations

import platform
import shutil
import subprocess


def detect_local_env() -> dict[str, str]:
    """检测用户本地开发环境，返回版本信息字典.

    所有 subprocess 调用 timeout=3s，总耗时 < 2s。
    """
    env: dict[str, str] = {
        "python_version": platform.python_version(),
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
    }

    node_ver = _get_cmd_version(["node", "--version"])
    if node_ver:
        env["node_version"] = node_ver

    for cmd in ("pip", "pip3", "npm", "yarn", "pnpm", "poetry", "uv"):
        if shutil.which(cmd):
            env[f"has_{cmd}"] = "true"

    return env


def format_env_for_context(env: dict[str, str]) -> str:
    """把检测结果格式化为 markdown 块，供 LLM prompt 使用."""
    lines = [
        "## 用户本地开发环境",
        f"- Python: {env.get('python_version', 'unknown')}",
        f"- OS: {env.get('os', 'unknown')}",
    ]

    node = env.get("node_version")
    if node:
        lines.append(f"- Node.js: {node}")

    managers = [k.replace("has_", "") for k, v in env.items()
                if k.startswith("has_") and v == "true"]
    if managers:
        lines.append(f"- 可用包管理器: {', '.join(managers)}")

    lines.append("")

    py_major, py_minor = env.get("python_version", "3.12").split(".")[:2]
    py_ver = int(py_major) * 100 + int(py_minor)
    hints = []
    if py_ver < 310:
        hints.append(
            f"Python {env['python_version']} 不支持 match/case 语法和 X | Y 类型联合写法，"
            "必须使用 if/elif 和 Optional[X]（需 from __future__ import annotations 或 from typing import Optional）。"
        )
    if py_ver < 311:
        hints.append(
            f"Python {env['python_version']} 不支持 ExceptionGroup 和 except*。"
        )

    if hints:
        lines.append("### 兼容性约束（必须遵守）")
        for h in hints:
            lines.append(f"- {h}")
    else:
        lines.append("生成代码时必须兼容以上环境版本。")

    return "\n".join(lines)


def _get_cmd_version(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
