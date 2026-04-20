"""ContextPinManager — 文件上下文 pin 管理 + activity 追踪.

用户 pin 文件/目录 → 每轮对话自动注入到 Agent 上下文。
"""

from __future__ import annotations

import logging
import os
import pathlib
import time
import uuid
from typing import Any, Optional

import aiosqlite

logger = logging.getLogger(__name__)

# 跳过的目录
_SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".egg-info",
}

# 文件扩展名 → 代码块语言
_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".md": "markdown", ".sh": "bash",
    ".sql": "sql", ".html": "html", ".css": "css",
}

# 总注入字符预算
MAX_CONTEXT_BUDGET = 50_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_pins (
    pin_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    pin_type TEXT NOT NULL DEFAULT 'file',
    label TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    max_lines INTEGER DEFAULT 200,
    active INTEGER DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pins_active ON context_pins(active, priority DESC);

CREATE TABLE IF NOT EXISTS file_activity (
    activity_id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    action TEXT NOT NULL,
    tool_name TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    timestamp REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_time ON file_activity(timestamp DESC);
"""


class ContextPinManager:
    """管理 pinned 文件上下文和 Agent 文件操作记录."""

    def __init__(self, workspace_dir: str, db_path: str = ""):
        self._workspace_dir = pathlib.Path(workspace_dir).resolve()
        self._db_path = db_path or str(self._workspace_dir / ".agent" / "context.db")
        self._db: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        """初始化数据库."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ── Pin CRUD ──

    async def add_pin(
        self,
        path: str,
        pin_type: str = "file",
        label: str = "",
        priority: int = 0,
        max_lines: int = 200,
    ) -> dict:
        """添加一个 pin."""
        # pin_type 校验
        if pin_type not in ("file", "directory"):
            pin_type = "file"

        # 沙箱检查
        target = (self._workspace_dir / path).resolve()
        if not target.is_relative_to(self._workspace_dir):
            raise ValueError("Path outside workspace")

        # 去重: 同路径不重复 pin
        cursor = await self._db.execute(
            "SELECT pin_id FROM context_pins WHERE path = ?", (path,)
        )
        existing = await cursor.fetchone()
        if existing:
            return {"pin_id": existing[0], "duplicate": True}

        now = time.time()
        pin_id = str(uuid.uuid4())[:8]
        await self._db.execute(
            "INSERT INTO context_pins (pin_id, path, pin_type, label, priority, max_lines, active, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
            (pin_id, path, pin_type, label, priority, max_lines, now, now),
        )
        await self._db.commit()
        return {"pin_id": pin_id, "duplicate": False}

    async def remove_pin(self, pin_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM context_pins WHERE pin_id = ?", (pin_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def update_pin(self, pin_id: str, **kwargs) -> bool:
        allowed = {"label", "priority", "max_lines", "active"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return False
        # 类型强制
        if "active" in updates:
            updates["active"] = int(bool(updates["active"]))
        if "priority" in updates:
            updates["priority"] = int(updates["priority"])
        if "max_lines" in updates:
            updates["max_lines"] = max(1, min(2000, int(updates["max_lines"])))
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [pin_id]
        cursor = await self._db.execute(
            f"UPDATE context_pins SET {set_clause} WHERE pin_id = ?", values
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_pins(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT pin_id, path, pin_type, label, priority, max_lines, active, created_at FROM context_pins ORDER BY priority DESC, created_at ASC"
        )
        rows = await cursor.fetchall()
        result = []
        for r in rows:
            target = (self._workspace_dir / r[1]).resolve()
            exists = target.exists()
            result.append({
                "pin_id": r[0], "path": r[1], "pin_type": r[2],
                "label": r[3], "priority": r[4], "max_lines": r[5],
                "active": bool(r[6]), "created_at": r[7], "exists": exists,
            })
        return result

    async def reorder_pins(self, pin_ids: list[str]) -> bool:
        for i, pid in enumerate(reversed(pin_ids)):
            await self._db.execute(
                "UPDATE context_pins SET priority = ?, updated_at = ? WHERE pin_id = ?",
                (i, time.time(), pid),
            )
        await self._db.commit()
        return True

    # ── 上下文生成 ──

    async def get_pinned_context(self) -> str:
        """读取所有 active pin 的文件内容，返回格式化字符串."""
        cursor = await self._db.execute(
            "SELECT path, pin_type, max_lines FROM context_pins WHERE active = 1 ORDER BY priority DESC"
        )
        pins = await cursor.fetchall()
        if not pins:
            return ""

        parts = ["## 参考文件\n"]
        budget = MAX_CONTEXT_BUDGET

        for path, pin_type, max_lines in pins:
            if budget <= 0:
                parts.append("\n> (已达上下文预算上限，后续文件已省略)")
                break

            target = (self._workspace_dir / path).resolve()
            # 沙箱检查
            if not target.is_relative_to(self._workspace_dir):
                continue
            if not target.exists():
                parts.append(f"\n### {path}\n> (文件不存在)\n")
                continue

            if pin_type == "directory":
                chunk = self._format_directory(target, path)
            else:
                chunk = self._format_file(target, path, max_lines)

            if len(chunk) > budget:
                chunk = chunk[:budget] + "\n...(截断)"
            budget -= len(chunk)
            parts.append(chunk)

        return "\n".join(parts)

    def _format_file(self, target: pathlib.Path, rel_path: str, max_lines: int) -> str:
        """格式化单个文件内容."""
        try:
            raw = target.read_bytes()
            if b'\x00' in raw[:4096]:
                return f"\n### {rel_path}\n> (二进制文件)\n"
            text = raw.decode("utf-8", errors="replace")
        except Exception as e:
            return f"\n### {rel_path}\n> (读取失败: {e})\n"

        all_lines = text.splitlines()
        total = len(all_lines)
        truncated = total > max_lines
        lines = all_lines[:max_lines] if truncated else all_lines

        lang = _EXT_LANG.get(target.suffix, "")
        content = "\n".join(lines)
        suffix = f"\n... (共 {total} 行, 显示前 {max_lines} 行)" if truncated else ""
        return f"\n### {rel_path}\n```{lang}\n{content}\n```{suffix}\n"

    def _format_directory(self, target: pathlib.Path, rel_path: str) -> str:
        """格式化目录列表."""
        entries = []
        try:
            for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                if item.name.startswith(".") or item.name in _SKIP_DIRS:
                    continue
                icon = "📁" if item.is_dir() else "📄"
                entries.append(f"  {icon} {item.name}")
        except PermissionError:
            return f"\n### {rel_path}/\n> (权限不足)\n"

        listing = "\n".join(entries[:50])
        more = f"\n  ... (共 {len(entries)} 项)" if len(entries) > 50 else ""
        return f"\n### {rel_path}/\n```\n{listing}{more}\n```\n"

    # ── Activity 追踪 ──

    async def record_activity(
        self, path: str, action: str, tool_name: str = "", summary: str = "",
    ) -> None:
        """记录 Agent 文件操作."""
        await self._db.execute(
            "INSERT INTO file_activity (activity_id, path, action, tool_name, summary, timestamp) VALUES (?,?,?,?,?,?)",
            (str(uuid.uuid4())[:8], path, action, tool_name, summary[:200], time.time()),
        )
        # 保留最近 200 条
        await self._db.execute(
            "DELETE FROM file_activity WHERE activity_id NOT IN (SELECT activity_id FROM file_activity ORDER BY timestamp DESC LIMIT 200)"
        )
        await self._db.commit()

    async def get_recent_activity(self, limit: int = 30) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT activity_id, path, action, tool_name, summary, timestamp FROM file_activity ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
        return [
            {"activity_id": r[0], "path": r[1], "action": r[2],
             "tool_name": r[3], "summary": r[4], "timestamp": r[5]}
            for r in rows
        ]

    # ── 文件建议 ──

    async def suggest_files(self, query: str) -> list[dict]:
        """基于关键词匹配建议文件 (不调 LLM)."""
        words = set(w.lower() for w in query.split() if len(w) >= 2)
        if not words:
            return []

        scored = []
        for item in self._walk_workspace(max_depth=3):
            name_lower = item["name"].lower()
            path_lower = item["path"].lower()
            score = sum(1 for w in words if w in name_lower or w in path_lower)
            if score > 0:
                scored.append({**item, "score": score})

        scored.sort(key=lambda x: -x["score"])
        return scored[:5]

    def _walk_workspace(self, max_depth: int = 3) -> list[dict]:
        """遍历工作目录."""
        results = []
        base = self._workspace_dir

        def _walk(directory: pathlib.Path, depth: int):
            if depth > max_depth:
                return
            try:
                for item in sorted(directory.iterdir()):
                    if item.name.startswith(".") or item.name in _SKIP_DIRS:
                        continue
                    rel = str(item.relative_to(base))
                    results.append({
                        "name": item.name,
                        "path": rel,
                        "is_dir": item.is_dir(),
                    })
                    if item.is_dir():
                        _walk(item, depth + 1)
            except PermissionError:
                pass

        _walk(base, 0)
        return results
