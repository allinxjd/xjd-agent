"""XjdHub 本地技能索引 (SQLite) — 新 schema，兼容 seed_hub.py 数据."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_NEW_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hub_users (
    user_id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT NOT NULL,
    password_hash TEXT NOT NULL, role TEXT DEFAULT 'user', display_name TEXT DEFAULT '',
    public_key TEXT DEFAULT '', balance REAL DEFAULT 0.0,
    created_at REAL, last_login REAL, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS hub_skills (
    skill_id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT UNIQUE NOT NULL,
    description TEXT DEFAULT '', author_id TEXT, version TEXT DEFAULT '1.0.0',
    category TEXT DEFAULT 'general', tags TEXT DEFAULT '[]', tools TEXT DEFAULT '[]',
    price REAL DEFAULT 0.0, status TEXT DEFAULT 'pending_review',
    content TEXT DEFAULT '', content_hash TEXT DEFAULT '', signature TEXT DEFAULT '',
    downloads INTEGER DEFAULT 0, rating_avg REAL DEFAULT 0.0, rating_count INTEGER DEFAULT 0,
    installed INTEGER DEFAULT 0, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS hub_skill_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, skill_id TEXT, version TEXT NOT NULL,
    content TEXT NOT NULL, content_hash TEXT DEFAULT '', signature TEXT DEFAULT '',
    changelog TEXT DEFAULT '', created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_skills_slug ON hub_skills(slug);
CREATE INDEX IF NOT EXISTS idx_skills_category ON hub_skills(category);
CREATE INDEX IF NOT EXISTS idx_skills_status ON hub_skills(status);
"""

SEED_USER_ID = "xjd-official-001"
SEED_USERNAME = "xjd-official"
_SORT_MAP = {
    "downloads": "downloads DESC",
    "rating": "rating_avg DESC",
    "newest": "created_at DESC",
    "name": "name ASC",
}


class HubIndex:
    """本地技能索引 — 查询 hub.db 中的种子/社区技能."""

    def __init__(self, db_path: str = ""):
        if not db_path:
            from agent.core.config import get_home
            db_path = str(get_home() / "hub.db")
        self._db_path = db_path
        self._db = None

    async def initialize(self) -> None:
        import aiosqlite
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        needs_new = await self._needs_schema_upgrade()
        if needs_new:
            await self._db.executescript(_NEW_SCHEMA_SQL)
            await self._db.commit()

        count = await self._count_approved()
        if count == 0:
            await self._auto_seed()

    async def _needs_schema_upgrade(self) -> bool:
        try:
            cursor = await self._db.execute("PRAGMA table_info(hub_skills)")
            cols = {row[1] for row in await cursor.fetchall()}
            if not cols:
                return True
            return "slug" not in cols
        except Exception:
            return True

    async def _count_approved(self) -> int:
        try:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM hub_skills WHERE status='approved'"
            )
            row = await cursor.fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
    async def _auto_seed(self) -> None:
        """首次启动自动导入内置技能."""
        import uuid
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not installed, skip auto-seed")
            return

        skills_dir = Path(__file__).parent.parent.parent / "skills"
        if not skills_dir.is_dir():
            return

        now = time.time()
        await self._db.execute(
            "INSERT OR IGNORE INTO hub_users (user_id, username, email, password_hash, role, created_at, last_login) "
            "VALUES (?, ?, ?, ?, 'admin', ?, ?)",
            (SEED_USER_ID, SEED_USERNAME, "team@xjd.ai", "seed:nologin", now, now),
        )

        count = 0
        for cat_dir in sorted(skills_dir.iterdir()):
            if not cat_dir.is_dir() or cat_dir.name.startswith("."):
                continue
            skill_md = cat_dir / "SKILL.md"
            if skill_md.exists():
                count += await self._seed_skill_md(skill_md, cat_dir.name, now)
                continue
            for yf in sorted(cat_dir.glob("*.yaml")):
                count += await self._seed_skill_yaml(yf, cat_dir.name, now)

        await self._db.commit()
        logger.info("Auto-seeded %d skills into hub.db", count)

    async def _seed_skill_md(self, path: Path, fallback_cat: str, now: float) -> int:
        import yaml
        text = path.read_text(encoding="utf-8")
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
        if not m:
            return 0
        meta = yaml.safe_load(m.group(1)) or {}
        name = meta.get("name", "")
        if not name:
            return 0
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64] or "skill"
        return await self._insert_seed(slug, name, meta.get("description", ""),
            meta.get("category", fallback_cat), meta.get("tags", []),
            meta.get("tools", []), str(meta.get("version", "1.0.0")), text, now)

    async def _seed_skill_yaml(self, path: Path, fallback_cat: str, now: float) -> int:
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not data or not data.get("name"):
            return 0
        name = data["name"]
        slug = re.sub(r"[^a-z0-9]+", "-", (data.get("skill_id") or name).lower()).strip("-")[:64]
        tools = [s.get("tool", "") for s in data.get("steps", []) if s.get("tool")]
        return await self._insert_seed(slug, name, data.get("description", ""),
            data.get("category", fallback_cat), data.get("tags", []),
            tools, str(data.get("version", "1")), path.read_text(encoding="utf-8"), now)

    async def _insert_seed(self, slug, name, desc, category, tags, tools, version, content, now) -> int:
        import uuid
        existing = await self._db.execute("SELECT skill_id FROM hub_skills WHERE slug=?", (slug,))
        if await existing.fetchone():
            return 0
        skill_id = uuid.uuid4().hex[:16]
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        await self._db.execute(
            "INSERT INTO hub_skills (skill_id,name,slug,description,author_id,version,category,"
            "tags,tools,price,status,content,content_hash,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0.0,'approved',?,?,?,?)",
            (skill_id, name, slug, desc, SEED_USER_ID, version, category,
             json.dumps(tags, ensure_ascii=False), json.dumps(tools, ensure_ascii=False),
             content, content_hash, now, now))
        return 1
    async def search(self, query: str = "", category: str = "", sort: str = "downloads",
                     page: int = 1, per_page: int = 20) -> list[dict[str, Any]]:
        conditions = ["status = 'approved'"]
        params: list[Any] = []
        if query:
            conditions.append("(name LIKE ? OR description LIKE ? OR tags LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        if category:
            conditions.append("category = ?")
            params.append(category)
        where = " AND ".join(conditions)
        sort_col = _SORT_MAP.get(sort, "downloads DESC")
        offset = (page - 1) * per_page
        params.extend([per_page, offset])

        cursor = await self._db.execute(
            f"SELECT skill_id, name, slug, description, author_id, version, category, "
            f"tags, tools, price, downloads, rating_avg, rating_count, installed, created_at "
            f"FROM hub_skills WHERE {where} ORDER BY {sort_col} LIMIT ? OFFSET ?",
            tuple(params))
        rows = [dict(r) for r in await cursor.fetchall()]
        for r in rows:
            for k in ("tags", "tools"):
                if isinstance(r.get(k), str):
                    try: r[k] = json.loads(r[k])
                    except Exception: r[k] = []
        return rows

    async def get(self, slug: str) -> Optional[dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT * FROM hub_skills WHERE slug = ?", (slug,))
        row = await cursor.fetchone()
        if not row:
            return None
        r = dict(row)
        for k in ("tags", "tools"):
            if isinstance(r.get(k), str):
                try: r[k] = json.loads(r[k])
                except Exception: r[k] = []
        r.pop("content", None)
        return r

    async def get_content(self, slug: str) -> Optional[str]:
        cursor = await self._db.execute(
            "SELECT content FROM hub_skills WHERE slug = ? AND status = 'approved'", (slug,))
        row = await cursor.fetchone()
        return row[0] if row else None

    async def categories(self) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT category, COUNT(*) as count FROM hub_skills "
            "WHERE status='approved' GROUP BY category ORDER BY count DESC")
        return [dict(r) for r in await cursor.fetchall()]

    async def featured(self, limit: int = 20) -> list[dict[str, Any]]:
        return await self.search(sort="downloads", per_page=limit)

    async def mark_installed(self, slug: str) -> None:
        await self._db.execute(
            "UPDATE hub_skills SET installed = 1 WHERE slug = ?", (slug,))
        await self._db.commit()

    async def list_installed(self) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT skill_id, name, slug, description, category, version, tags "
            "FROM hub_skills WHERE installed = 1 ORDER BY name")
        return [dict(r) for r in await cursor.fetchall()]

    async def total_count(self) -> int:
        return await self._count_approved()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
