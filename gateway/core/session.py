"""会话管理器 — 每个用户拥有独立的 Session，跨平台共享对话上下文。
同一个用户在微信和 Telegram 上的对话会合并到同一个 Session。

功能:
- Session 创建、查找、恢复
- 用户到 Session 的映射 (支持跨平台)
- Session 持久化 (SQLite)
- DM 配对策略 (pairing / open)
- Session 超时自动清理
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Optional

from gateway.platforms.base import PlatformType

logger = logging.getLogger(__name__)

@dataclass
class Session:
    """用户会话.

    一个 Session 代表一个用户的完整对话上下文，
    可跨多个平台 (如用户同时使用微信和 Telegram)。
    """

    session_id: str
    user_id: str
    created_at: float = 0.0
    updated_at: float = 0.0
    platform: PlatformType = PlatformType.WEB
    chat_id: str = ""
    is_active: bool = True

    # 对话历史 (Message 列表的 JSON)
    messages: list[dict[str, Any]] = field(default_factory=list)
    MAX_SESSION_MESSAGES: ClassVar[int] = 200

    # 用户偏好/元数据
    metadata: dict[str, Any] = field(default_factory=dict)

    # 关联的平台信息
    platform_bindings: dict[str, str] = field(default_factory=dict)
    # { "telegram": "chat_id_123", "wechat": "chat_id_456" }

    # 统计
    message_count: int = 0
    tool_calls_count: int = 0
    total_tokens: int = 0

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """添加消息到历史."""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": time.time(),
            **kwargs,
        })
        if role == "user":
            self.message_count += 1
        self.updated_at = time.time()
        # 超过上限截断，保留最近的消息
        if len(self.messages) > self.MAX_SESSION_MESSAGES:
            self.messages = self.messages[-self.MAX_SESSION_MESSAGES:]

    def to_dict(self) -> dict[str, Any]:
        """序列化."""
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "platform": self.platform.value,
            "chat_id": self.chat_id,
            "is_active": self.is_active,
            "messages": self.messages,
            "metadata": self.metadata,
            "platform_bindings": self.platform_bindings,
            "message_count": self.message_count,
            "tool_calls_count": self.tool_calls_count,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        """反序列化."""
        return cls(
            session_id=data["session_id"],
            user_id=data["user_id"],
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            platform=PlatformType(data.get("platform", "web")),
            chat_id=data.get("chat_id", ""),
            is_active=data.get("is_active", True),
            messages=data.get("messages", []),
            metadata=data.get("metadata", {}),
            platform_bindings=data.get("platform_bindings", {}),
            message_count=data.get("message_count", 0),
            tool_calls_count=data.get("tool_calls_count", 0),
            total_tokens=data.get("total_tokens", 0),
        )

class SessionManager:
    """会话管理器.

    负责:
    1. 管理用户到 Session 的映射
    2. Session 持久化 (SQLite)
    3. Session 超时清理
    4. DM 配对策略

    DM 策略:
    - "pairing": 每个平台+chat_id 独立 Session (默认)
    - "open": 同一用户跨平台共享 Session
    """

    def __init__(
        self,
        dm_policy: str = "pairing",
        session_timeout: int = 3600 * 24,  # 24小时
        max_sessions: int = 10000,
        db_path: Optional[str] = None,
    ) -> None:
        self._dm_policy = dm_policy
        self._session_timeout = session_timeout
        self._max_sessions = max_sessions
        self._db_path = db_path

        # 内存缓存
        self._sessions: dict[str, Session] = {}
        # user_id -> session_id 映射
        self._user_sessions: dict[str, str] = {}
        # platform:chat_id -> session_id 映射
        self._chat_sessions: dict[str, str] = {}

        self._db = None  # SQLite connection
        self._initialized = False

    async def _ensure_db(self) -> None:
        """确保数据库已初始化."""
        if self._initialized:
            return

        if self._db_path:
            import aiosqlite
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    data TEXT NOT NULL,
                    created_at REAL,
                    updated_at REAL,
                    is_active INTEGER DEFAULT 1
                )
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                ON sessions(user_id)
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_active
                ON sessions(is_active, updated_at)
            """)
            await self._db.commit()

        self._initialized = True

    async def get_or_create(
        self,
        user_id: str,
        platform: PlatformType,
        chat_id: str,
    ) -> Session:
        """获取或创建 Session.

        根据 DM 策略决定是否复用现有 Session:
        - "pairing": 同一 platform + chat_id 复用
        - "open": 同一 user_id 跨平台复用
        """
        await self._ensure_db()

        if self._dm_policy == "open":
            # 同一用户共享 Session
            if user_id in self._user_sessions:
                session_id = self._user_sessions[user_id]
                session = self._sessions.get(session_id)
                if session and session.is_active:
                    # 绑定新平台
                    session.platform_bindings[platform.value] = chat_id
                    session.updated_at = time.time()
                    return session
        else:
            # pairing: 每个 platform:chat_id 独立
            chat_key = f"{platform.value}:{chat_id}"
            if chat_key in self._chat_sessions:
                session_id = self._chat_sessions[chat_key]
                session = self._sessions.get(session_id)
                if session and session.is_active:
                    session.updated_at = time.time()
                    return session

        # 创建新 Session
        session = Session(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            platform=platform,
            chat_id=chat_id,
            created_at=time.time(),
            updated_at=time.time(),
            platform_bindings={platform.value: chat_id},
        )

        self._sessions[session.session_id] = session
        self._user_sessions[user_id] = session.session_id
        chat_key = f"{platform.value}:{chat_id}"
        self._chat_sessions[chat_key] = session.session_id

        # 持久化
        await self._persist_session(session)

        logger.info(
            "Created session %s for user %s on %s",
            session.session_id[:8], user_id, platform.value,
        )

        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """获取 Session."""
        return self._sessions.get(session_id)

    async def get_user_session(self, user_id: str) -> Optional[Session]:
        """获取用户当前活跃的 Session."""
        session_id = self._user_sessions.get(user_id)
        if session_id:
            return self._sessions.get(session_id)
        return None

    async def end_session(self, session_id: str) -> None:
        """结束 Session."""
        session = self._sessions.get(session_id)
        if session:
            session.is_active = False
            session.updated_at = time.time()
            await self._persist_session(session)

    async def reset_session(self, session_id: str) -> Optional[Session]:
        """重置 Session (清空对话历史)."""
        session = self._sessions.get(session_id)
        if session:
            session.messages = []
            session.message_count = 0
            session.tool_calls_count = 0
            session.total_tokens = 0
            session.updated_at = time.time()
            await self._persist_session(session)
        return session

    async def list_sessions(
        self,
        active_only: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列出所有 Session (概要信息)."""
        sessions = []
        for s in sorted(
            self._sessions.values(),
            key=lambda x: x.updated_at,
            reverse=True,
        ):
            if active_only and not s.is_active:
                continue
            sessions.append({
                "session_id": s.session_id,
                "user_id": s.user_id,
                "platform": s.platform.value,
                "message_count": s.message_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "is_active": s.is_active,
            })
            if len(sessions) >= limit:
                break
        return sessions

    async def cleanup_expired(self) -> int:
        """清理过期 Session — 持久化后从内存中删除."""
        now = time.time()
        expired = []
        for session_id, session in list(self._sessions.items()):
            if session.is_active and (now - session.updated_at) > self._session_timeout:
                expired.append(session_id)

        for session_id in expired:
            session = self._sessions[session_id]
            session.is_active = False
            await self._persist_session(session)
            # 从内存中删除，释放资源
            del self._sessions[session_id]
            # 清理索引
            if session.user_id in self._user_sessions:
                if self._user_sessions[session.user_id] == session_id:
                    del self._user_sessions[session.user_id]
            chat_key = f"{session.platform.value}:{session.chat_id}"
            if chat_key in self._chat_sessions:
                if self._chat_sessions[chat_key] == session_id:
                    del self._chat_sessions[chat_key]

        # 强制上限: 如果内存中 session 过多，驱逐最旧的不活跃 session
        if len(self._sessions) > self._max_sessions:
            by_age = sorted(
                [(sid, s) for sid, s in self._sessions.items() if not s.is_active],
                key=lambda x: x[1].updated_at,
            )
            for sid, s in by_age[:len(self._sessions) - self._max_sessions]:
                del self._sessions[sid]

        if expired:
            logger.info("Cleaned up %d expired sessions, %d remain in memory", len(expired), len(self._sessions))

        return len(expired)

    async def save_all(self) -> None:
        """保存所有 Session 到数据库."""
        for session in self._sessions.values():
            await self._persist_session(session)
        logger.info("Saved %d sessions", len(self._sessions))

    # ── 持久化 ────────────────────────────────────────────────

    async def _persist_session(self, session: Session) -> None:
        """将 Session 持久化到 SQLite."""
        if not self._db:
            return

        data = json.dumps(session.to_dict(), ensure_ascii=False)
        await self._db.execute(
            """
            INSERT OR REPLACE INTO sessions
            (session_id, user_id, data, created_at, updated_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.user_id,
                data,
                session.created_at,
                session.updated_at,
                1 if session.is_active else 0,
            ),
        )
        await self._db.commit()

    async def _load_session(self, session_id: str) -> Optional[Session]:
        """从 SQLite 加载 Session."""
        if not self._db:
            return None

        cursor = await self._db.execute(
            "SELECT data FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row:
            try:
                data = json.loads(row[0])
                return Session.from_dict(data)
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning("Corrupted session %s: %s", session_id, e)
                return None
        return None

    async def _load_user_sessions(self, user_id: str) -> list[Session]:
        """加载用户的所有 Session."""
        if not self._db:
            return []

        cursor = await self._db.execute(
            "SELECT data FROM sessions WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC",
            (user_id,),
        )
        sessions = []
        async for row in cursor:
            data = json.loads(row[0])
            sessions.append(Session.from_dict(data))
        return sessions

    async def close(self) -> None:
        """关闭数据库连接."""
        if self._db:
            await self._db.close()
            self._db = None

    # ── Session Resume ──

    async def resume_session(self, session_id: str) -> Optional[Session]:
        """恢复历史会话 — 重新激活已结束的 Session 并恢复对话上下文.

        Args:
            session_id: 要恢复的 Session ID

        Returns:
            恢复后的 Session，如果不存在则返回 None
        """
        await self._ensure_db()

        # 先查内存
        session = self._sessions.get(session_id)

        # 再查数据库
        if not session:
            session = await self._load_session(session_id)

        if not session:
            return None

        # 重新激活
        session.is_active = True
        session.updated_at = time.time()

        # 更新缓存
        self._sessions[session_id] = session
        self._user_sessions[session.user_id] = session_id
        if session.chat_id:
            key = f"{session.platform.value}:{session.chat_id}"
            self._chat_sessions[key] = session_id

        await self._persist_session(session)
        logger.info("恢复会话 %s (用户: %s, %d 条消息)", session_id, session.user_id, session.message_count)
        return session

    async def search_sessions(
        self,
        keyword: str = "",
        user_id: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """搜索历史会话.

        Args:
            keyword: 搜索关键词 (匹配消息内容)
            user_id: 限定用户
            limit: 最大返回数
        """
        results = []
        candidates = sorted(self._sessions.values(), key=lambda x: x.updated_at, reverse=True)

        # 也从数据库加载
        if self._db and user_id:
            db_sessions = await self._load_user_sessions_all(user_id)
            seen = {s.session_id for s in candidates}
            for s in db_sessions:
                if s.session_id not in seen:
                    candidates.append(s)

        for s in candidates:
            if user_id and s.user_id != user_id:
                continue
            if keyword:
                match = any(keyword.lower() in str(m.get("content", "")).lower() for m in s.messages)
                if not match:
                    continue
            results.append({
                "session_id": s.session_id,
                "user_id": s.user_id,
                "platform": s.platform.value,
                "message_count": s.message_count,
                "created_at": s.created_at,
                "updated_at": s.updated_at,
                "is_active": s.is_active,
                "preview": s.messages[-1].get("content", "")[:100] if s.messages else "",
            })
            if len(results) >= limit:
                break
        return results

    async def _load_user_sessions_all(self, user_id: str) -> list[Session]:
        """加载用户的所有 Session (包括非活跃)."""
        if not self._db:
            return []
        cursor = await self._db.execute(
            "SELECT data FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        sessions = []
        async for row in cursor:
            data = json.loads(row[0])
            sessions.append(Session.from_dict(data))
        return sessions
