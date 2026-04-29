"""CompanyStore — SQLite 持久化，支持任务断点续跑和消息历史."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from agent.company.message import CompanyMessage
from agent.company.task import CompanyTask

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".xjd-agent" / "company.db"


class CompanyStore:
    """SQLite 持久化层."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def open(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _create_tables(self) -> None:
        assert self._conn
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                expected_output TEXT NOT NULL DEFAULT '',
                assigned_to TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                max_retries INTEGER NOT NULL DEFAULT 2,
                result TEXT NOT NULL DEFAULT '',
                retry_count INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                msg_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL,
                cause_by TEXT NOT NULL DEFAULT '',
                sent_from TEXT NOT NULL DEFAULT '',
                send_to TEXT NOT NULL DEFAULT '',
                timestamp REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_messages_task ON messages(task_id);
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                requirement TEXT NOT NULL,
                task_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'running',
                started_at REAL NOT NULL,
                finished_at REAL,
                total_rounds INTEGER NOT NULL DEFAULT 0,
                result_summary TEXT NOT NULL DEFAULT ''
            );
        """)

    def save_task(self, task: CompanyTask) -> None:
        assert self._conn
        now = time.time()
        self._conn.execute(
            """INSERT OR REPLACE INTO tasks
               (task_id, title, description, expected_output, assigned_to,
                status, max_retries, result, retry_count, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.task_id, task.title, task.description, task.expected_output,
             task.assigned_to, task.status, task.max_retries, task.result,
             task.retry_count, json.dumps(task.metadata), now, now),
        )
        self._conn.commit()

    def load_task(self, task_id: str) -> Optional[CompanyTask]:
        assert self._conn
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def list_tasks(self, status: Optional[str] = None) -> list[CompanyTask]:
        assert self._conn
        if status:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def update_task_status(self, task_id: str, status: str, result: str = "") -> None:
        assert self._conn
        if result:
            self._conn.execute(
                "UPDATE tasks SET status = ?, result = ?, updated_at = ? WHERE task_id = ?",
                (status, result, time.time(), task_id),
            )
        else:
            self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, time.time(), task_id),
            )
        self._conn.commit()

    def save_message(self, msg: CompanyMessage) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT OR IGNORE INTO messages
               (msg_id, task_id, content, cause_by, sent_from, send_to, timestamp, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg.msg_id, msg.task_id, msg.content, msg.cause_by,
             msg.sent_from, msg.send_to, msg.timestamp, json.dumps(msg.metadata)),
        )
        self._conn.commit()

    def list_messages(self, task_id: str = "", limit: int = 100) -> list[CompanyMessage]:
        assert self._conn
        if task_id:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE task_id = ? ORDER BY timestamp DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM messages ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def save_run(self, run_id: str, requirement: str, task_id: str) -> None:
        assert self._conn
        self._conn.execute(
            """INSERT OR REPLACE INTO runs
               (run_id, requirement, task_id, status, started_at)
               VALUES (?, ?, ?, 'running', ?)""",
            (run_id, requirement, task_id, time.time()),
        )
        self._conn.commit()

    def finish_run(self, run_id: str, status: str, total_rounds: int, result_summary: str) -> None:
        assert self._conn
        self._conn.execute(
            """UPDATE runs SET status = ?, finished_at = ?, total_rounds = ?, result_summary = ?
               WHERE run_id = ?""",
            (status, time.time(), total_rounds, result_summary[:2000], run_id),
        )
        self._conn.commit()

    def list_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        assert self._conn
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_resumable_run(self) -> Optional[dict[str, Any]]:
        """获取最近一个未完成的 run，用于断点续跑."""
        assert self._conn
        row = self._conn.execute(
            "SELECT * FROM runs WHERE status = 'running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> CompanyTask:
        return CompanyTask(
            task_id=row["task_id"],
            title=row["title"],
            description=row["description"],
            expected_output=row["expected_output"],
            assigned_to=row["assigned_to"],
            status=row["status"],
            max_retries=row["max_retries"],
            result=row["result"],
            retry_count=row["retry_count"],
            metadata=json.loads(row["metadata"]),
        )

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> CompanyMessage:
        return CompanyMessage(
            content=row["content"],
            cause_by=row["cause_by"],
            sent_from=row["sent_from"],
            send_to=row["send_to"],
            task_id=row["task_id"],
            msg_id=row["msg_id"],
            timestamp=row["timestamp"],
            metadata=json.loads(row["metadata"]),
        )
