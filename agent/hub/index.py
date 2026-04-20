"""XjdHub 本地技能索引 (SQLite)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class HubIndex:
    """本地技能索引 — 存储已发布/已安装技能的元数据."""

    def __init__(self, db_path: str = ""):
        if not db_path:
            from agent.core.config import get_home
            db_path = str(get_home() / "hub.db")
        self._db_path = db_path
        self._db = None

    async def initialize(self) -> None:
        import aiosqlite
        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS hub_skills (
                name TEXT PRIMARY KEY,
                version TEXT NOT NULL,
                author TEXT DEFAULT '',
                description TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                price REAL DEFAULT 0,
                downloads INTEGER DEFAULT 0,
                checksum TEXT DEFAULT '',
                published_at REAL DEFAULT 0,
                installed_at REAL DEFAULT 0,
                manifest TEXT DEFAULT '{}'
            )
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_hub_tags ON hub_skills(tags)
        """)
        await self._db.commit()

    async def add(self, manifest: dict[str, Any]) -> None:
        name = manifest.get("name", "")
        if not name:
            return
        await self._db.execute(
            """INSERT OR REPLACE INTO hub_skills
               (name, version, author, description, tags, price, downloads,
                checksum, published_at, manifest)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                manifest.get("version", "1.0.0"),
                manifest.get("author", ""),
                manifest.get("description", ""),
                json.dumps(manifest.get("tags", []), ensure_ascii=False),
                float(manifest.get("price", 0)),
                int(manifest.get("downloads", 0)),
                manifest.get("checksum", ""),
                manifest.get("published_at", time.time()),
                json.dumps(manifest, ensure_ascii=False),
            ),
        )
        await self._db.commit()

    async def search(
        self, query: str = "", category: str = "", limit: int = 20,
    ) -> list[dict[str, Any]]:
        results = []
        if query:
            cursor = await self._db.execute(
                """SELECT manifest FROM hub_skills
                   WHERE name LIKE ? OR description LIKE ? OR tags LIKE ?
                   ORDER BY downloads DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", f"%{query}%", limit),
            )
        else:
            cursor = await self._db.execute(
                "SELECT manifest FROM hub_skills ORDER BY downloads DESC LIMIT ?",
                (limit,),
            )
        async for row in cursor:
            results.append(json.loads(row[0]))
        return results

    async def get(self, name: str) -> Optional[dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT manifest FROM hub_skills WHERE name = ?", (name,),
        )
        row = await cursor.fetchone()
        return json.loads(row[0]) if row else None

    async def remove(self, name: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM hub_skills WHERE name = ?", (name,),
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def mark_installed(self, name: str) -> None:
        await self._db.execute(
            "UPDATE hub_skills SET installed_at = ? WHERE name = ?",
            (time.time(), name),
        )
        await self._db.commit()

    async def list_installed(self) -> list[dict[str, Any]]:
        cursor = await self._db.execute(
            "SELECT manifest FROM hub_skills WHERE installed_at > 0 ORDER BY installed_at DESC",
        )
        return [json.loads(row[0]) async for row in cursor]

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
