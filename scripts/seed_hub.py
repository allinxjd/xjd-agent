#!/usr/bin/env python3
"""种子发布脚本 — 将内置技能批量写入 hub.db (status=approved)."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

SKILLS_DIR = Path(__file__).parent.parent / "skills"
DB_PATH = Path.home() / ".xjd-agent" / "hub.db"
SEED_USER_ID = "xjd-official-001"
SEED_USERNAME = "xjd-official"

SCHEMA_SQL = """
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
    created_at REAL, updated_at REAL
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


def _make_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:64]
    return slug or "skill"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, re.DOTALL)
    if not m:
        return {}, text
    import yaml
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


def load_skill_md(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    if not meta.get("name"):
        return None
    return {
        "name": meta["name"],
        "description": meta.get("description", ""),
        "version": str(meta.get("version", "1.0.0")),
        "category": meta.get("category", "general"),
        "tags": meta.get("tags", []),
        "tools": meta.get("tools", []),
        "trigger": meta.get("trigger", ""),
        "content": text,
        "slug": _make_slug(meta["name"]),
    }


def load_skill_yaml(path: Path) -> dict | None:
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data or not data.get("name"):
        return None
    return {
        "name": data["name"],
        "description": data.get("description", ""),
        "version": str(data.get("version", "1")),
        "category": data.get("category", "general"),
        "tags": data.get("tags", []),
        "tools": [s.get("tool", "") for s in data.get("steps", []) if s.get("tool")],
        "trigger": data.get("trigger", ""),
        "content": path.read_text(encoding="utf-8"),
        "slug": _make_slug(data.get("skill_id") or data["name"]),
    }


# PLACEHOLDER_SEED_MAIN

def load_all_skills() -> list[dict]:
    skills = []
    for category_dir in sorted(SKILLS_DIR.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        skill_md = category_dir / "SKILL.md"
        if skill_md.exists():
            s = load_skill_md(skill_md)
            if s:
                if s["category"] == "general":
                    s["category"] = category_dir.name
                skills.append(s)
            continue
        for yaml_file in sorted(category_dir.glob("*.yaml")):
            s = load_skill_yaml(yaml_file)
            if s:
                if s["category"] == "general":
                    s["category"] = category_dir.name
                skills.append(s)
    return skills


def seed(db_path: Path | None = None):
    db_path = db_path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA_SQL)

    now = time.time()
    conn.execute(
        "INSERT OR IGNORE INTO hub_users (user_id, username, email, password_hash, role, created_at, last_login) "
        "VALUES (?, ?, ?, ?, 'admin', ?, ?)",
        (SEED_USER_ID, SEED_USERNAME, "team@xjd.ai", "seed:nologin", now, now),
    )

    skills = load_all_skills()
    published, skipped, failed = 0, 0, 0

    for s in skills:
        existing = conn.execute("SELECT skill_id FROM hub_skills WHERE slug = ?", (s["slug"],)).fetchone()
        if existing:
            skipped += 1
            continue
        try:
            skill_id = uuid.uuid4().hex[:16]
            content_hash = hashlib.sha256(s["content"].encode()).hexdigest()
            conn.execute(
                "INSERT INTO hub_skills (skill_id, name, slug, description, author_id, version, "
                "category, tags, tools, price, status, content, content_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 'approved', ?, ?, ?, ?)",
                (skill_id, s["name"], s["slug"], s["description"], SEED_USER_ID,
                 s["version"], s["category"],
                 json.dumps(s["tags"], ensure_ascii=False),
                 json.dumps(s["tools"], ensure_ascii=False),
                 s["content"], content_hash, now, now),
            )
            conn.execute(
                "INSERT INTO hub_skill_versions (skill_id, version, content, content_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (skill_id, s["version"], s["content"], content_hash, now),
            )
            published += 1
        except Exception as e:
            print(f"  FAIL: {s['name']} — {e}")
            failed += 1

    conn.commit()
    conn.close()

    print(f"\nSeed complete: {published} published, {skipped} skipped, {failed} failed (total {len(skills)})")
    print(f"Database: {db_path}")


if __name__ == "__main__":
    seed()
