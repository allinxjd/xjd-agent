"""持久化审计日志 — 不可变的操作记录.

用法:
    audit = AuditLogger()
    await audit.initialize()
    await audit.log("tool_call", user="admin", detail="web_search: Python")
    entries = await audit.query(action="tool_call", limit=50)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class AuditEntry:
    """审计日志条目."""

    timestamp: float
    action: str
    user: str = ""
    detail: str = ""
    ip: str = ""
    session_id: str = ""
    integrity_hash: str = ""

class AuditLogger:
    """持久化审计日志 — 追加写入，不可修改."""

    def __init__(self, log_dir: Optional[Path] = None) -> None:
        from agent.core.config import get_home

        self._log_dir = log_dir or (get_home() / "audit")
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_file: Optional[Path] = None
        self._last_hash = "0" * 64

    async def initialize(self) -> None:
        """初始化，恢复最后的 hash 链."""
        existing = sorted(self._log_dir.glob("audit_*.jsonl"))
        if existing:
            last_file = existing[-1]
            lines = last_file.read_text(encoding="utf-8").strip().split("\n")
            if lines:
                try:
                    last_entry = json.loads(lines[-1])
                    self._last_hash = last_entry.get("integrity_hash", self._last_hash)
                except json.JSONDecodeError:
                    pass

    async def log(
        self,
        action: str,
        user: str = "",
        detail: str = "",
        ip: str = "",
        session_id: str = "",
    ) -> AuditEntry:
        """记录审计日志."""
        ts = time.time()

        # 计算完整性 hash (链式)
        payload = f"{self._last_hash}:{ts}:{action}:{user}:{detail}"
        integrity_hash = hashlib.sha256(payload.encode()).hexdigest()

        entry = AuditEntry(
            timestamp=ts,
            action=action,
            user=user,
            detail=detail,
            ip=ip,
            session_id=session_id,
            integrity_hash=integrity_hash,
        )

        # 追加写入日志文件 (按天分文件)
        from datetime import datetime

        date_str = datetime.fromtimestamp(ts).strftime("%Y%m%d")
        log_file = self._log_dir / f"audit_{date_str}.jsonl"

        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": entry.timestamp,
                "action": entry.action,
                "user": entry.user,
                "detail": entry.detail[:1000],
                "ip": entry.ip,
                "session_id": entry.session_id,
                "integrity_hash": entry.integrity_hash,
            }, ensure_ascii=False) + "\n")

        self._last_hash = integrity_hash
        return entry

    async def query(
        self,
        action: Optional[str] = None,
        user: Optional[str] = None,
        since: Optional[float] = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """查询审计日志."""
        entries: list[AuditEntry] = []

        for log_file in sorted(self._log_dir.glob("audit_*.jsonl"), reverse=True):
            for line in reversed(log_file.read_text(encoding="utf-8").strip().split("\n")):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if action and data.get("action") != action:
                    continue
                if user and data.get("user") != user:
                    continue
                if since and data.get("timestamp", 0) < since:
                    continue

                entries.append(AuditEntry(**data))
                if len(entries) >= limit:
                    return entries

        return entries

    async def verify_integrity(self) -> tuple[bool, int]:
        """验证审计日志完整性 (hash 链)."""
        prev_hash = "0" * 64
        total = 0
        valid = 0

        for log_file in sorted(self._log_dir.glob("audit_*.jsonl")):
            for line in log_file.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                total += 1
                try:
                    data = json.loads(line)
                    payload = (
                        f"{prev_hash}:{data['timestamp']}:{data['action']}"
                        f":{data['user']}:{data['detail']}"
                    )
                    expected = hashlib.sha256(payload.encode()).hexdigest()
                    if expected == data.get("integrity_hash"):
                        valid += 1
                    prev_hash = data.get("integrity_hash", prev_hash)
                except (json.JSONDecodeError, KeyError):
                    pass

        return valid == total, total

    async def get_stats(self) -> dict:
        """获取审计统计."""
        stats: dict[str, int] = {}
        total = 0
        for log_file in self._log_dir.glob("audit_*.jsonl"):
            for line in log_file.read_text(encoding="utf-8").strip().split("\n"):
                if not line:
                    continue
                total += 1
                try:
                    data = json.loads(line)
                    action = data.get("action", "unknown")
                    stats[action] = stats.get(action, 0) + 1
                except json.JSONDecodeError:
                    pass
        return {"total": total, "by_action": stats}
