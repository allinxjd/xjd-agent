"""聊天历史中的 API token/key 提取 — best-effort, 正则匹配."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

_TOKEN_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("GITHUB_TOKEN", re.compile(
        r"(?:github[_ ]?(?:token|pat|api[_ ]?key))[\s:=]*['\"]?(gh[ps]_[A-Za-z0-9_]{20,})['\"]?",
        re.IGNORECASE,
    )),
    ("GITHUB_TOKEN", re.compile(
        r"\b(gh[ps]_[A-Za-z0-9_]{20,})\b",
    )),
    ("OPENAI_API_KEY", re.compile(
        r"(?:openai[_ ]?(?:api[_ ]?)?key)[\s:=]*['\"]?(sk-[A-Za-z0-9_-]{20,})['\"]?",
        re.IGNORECASE,
    )),
    ("OPENAI_API_KEY", re.compile(
        r"\b(sk-[A-Za-z0-9_-]{20,})\b",
    )),
    ("ANTHROPIC_API_KEY", re.compile(
        r"\b(sk-ant-[A-Za-z0-9_-]{20,})\b",
    )),
    ("API_KEY", re.compile(
        r"(?:api[_ ]?key|token|secret)[\s:=]+['\"]?([A-Za-z0-9_-]{20,})['\"]?",
        re.IGNORECASE,
    )),
]


def extract_secrets(chat_history: list[tuple[str, str]]) -> dict[str, str]:
    """扫描聊天记录，提取 API token/key.

    返回 {环境变量名: token值}。同名 key 只取第一个匹配（更具体的模式优先）。
    """
    found: dict[str, str] = {}
    text = "\n".join(content for _, content in chat_history)

    for env_name, pattern in _TOKEN_PATTERNS:
        if env_name in found:
            continue
        matches = pattern.findall(text)
        if matches:
            value = matches[-1]
            if env_name == "API_KEY" and value in found.values():
                continue
            found[env_name] = value

    return found


def write_env_file(project_dir: Path, secrets: dict[str, str]) -> Optional[Path]:
    """写入 secrets 到 project_dir/.env，追加模式，不覆盖已有 key."""
    if not secrets:
        return None

    env_path = project_dir / ".env"
    existing = ""
    if env_path.exists():
        existing = env_path.read_text()

    lines = []
    for key, value in secrets.items():
        if f"{key}=" not in existing:
            lines.append(f"{key}={value}")

    if not lines:
        return env_path if existing else None

    with open(env_path, "a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n".join(lines) + "\n")

    return env_path
