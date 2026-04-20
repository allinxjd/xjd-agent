"""记忆系统 Provider — 多后端存储抽象.

记忆体系 (四层架构):
1. 工作记忆 (Working): 当前对话上下文中的短期记忆
2. 情景记忆 (Episodic): 过往对话的关键摘要
3. 语义记忆 (Semantic): 持久化的知识、事实和偏好
4. 程序记忆 (Procedural): 技能/流程 — 与 SkillManager 双向同步

Provider 抽象:
- BuiltinProvider: 内置 SQLite + FTS5 全文搜索
- RedisProvider: Redis 高性能缓存 + 搜索
- PostgreSQLProvider: PostgreSQL 关系型存储
- ChromaDBProvider: ChromaDB 向量搜索
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

class MemoryType(str, Enum):
    """记忆类型."""

    FACT = "fact"           # 事实型 (用户姓名、偏好、联系方式)
    PREFERENCE = "preference"  # 偏好型 (喜欢的回复风格、语言)
    SKILL = "skill"         # 技能型 (学会的工具用法、任务流程)
    PROCEDURAL = "procedural"  # 程序记忆 (技能步骤，与 SkillManager 同步)
    EPISODIC = "episodic"   # 情景型 (对话摘要、关键事件)
    CONTEXT = "context"     # 上下文型 (项目信息、当前任务)
    RELATIONSHIP = "relationship"  # 关系型 (用户间的关系)
    META = "meta"               # 元认知记忆 (自我反思、行为模式)

class MemoryImportance(str, Enum):
    """记忆重要度."""

    LOW = "low"       # 可能有用
    MEDIUM = "medium"  # 有用
    HIGH = "high"      # 重要
    CRITICAL = "critical"  # 必须记住

@dataclass
class Memory:
    """单条记忆.

    每条记忆包含:
    - 内容 (人类可读的描述)
    - 类型 (fact / preference / skill / episodic / context)
    - 元数据 (来源、时间、关联用户)
    - 重要度
    - 嵌入向量 (可选，用于语义搜索)
    """

    memory_id: str = ""
    content: str = ""
    memory_type: MemoryType = MemoryType.FACT
    importance: MemoryImportance = MemoryImportance.MEDIUM
    user_id: str = ""  # 关联的用户 ID (空 = 全局记忆)
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0  # 被检索次数
    usefulness_score: float = 0.5  # 有用性评分 (0~1, EMA 更新)
    feedback_count: int = 0  # 反馈次数
    embedding: Optional[list[float]] = None  # 向量嵌入

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "content": self.content,
            "memory_type": self.memory_type.value,
            "importance": self.importance.value,
            "user_id": self.user_id,
            "tags": self.tags,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Memory:
        return cls(
            memory_id=data.get("memory_id", ""),
            content=data.get("content", ""),
            memory_type=MemoryType(data.get("memory_type", "fact")),
            importance=MemoryImportance(data.get("importance", "medium")),
            user_id=data.get("user_id", ""),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            access_count=data.get("access_count", 0),
        )

@dataclass
class MemorySearchResult:
    """记忆搜索结果."""

    memory: Memory
    relevance_score: float = 0.0  # 0-1 相关度
    match_type: str = ""  # "fts" | "semantic" | "exact"

class BaseMemoryProvider(ABC):
    """记忆 Provider 基类.

    所有记忆存储后端实现此接口。
    """

    @abstractmethod
    async def initialize(self) -> None:
        """初始化 Provider (创建表、连接等)."""

    @abstractmethod
    async def store(self, memory: Memory) -> str:
        """存储记忆. Returns memory_id."""

    @abstractmethod
    async def retrieve(self, memory_id: str) -> Optional[Memory]:
        """按 ID 获取记忆."""

    @abstractmethod
    async def search(
        self,
        query: str,
        user_id: str = "",
        memory_types: Optional[list[MemoryType]] = None,
        limit: int = 10,
        min_importance: Optional[MemoryImportance] = None,
    ) -> list[MemorySearchResult]:
        """搜索相关记忆.

        Args:
            query: 搜索查询
            user_id: 限定用户 (空 = 搜索全局)
            memory_types: 限定类型
            limit: 最大返回数
            min_importance: 最低重要度

        Returns:
            按相关度排序的搜索结果
        """

    @abstractmethod
    async def update(self, memory_id: str, updates: dict[str, Any]) -> bool:
        """更新记忆."""

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """删除记忆."""

    @abstractmethod
    async def list_memories(
        self,
        user_id: str = "",
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Memory]:
        """列出记忆."""

    async def get_stats(self) -> dict[str, Any]:
        """获取统计信息."""
        return {}

    async def close(self) -> None:
        """关闭连接."""

class BuiltinMemoryProvider(BaseMemoryProvider):
    """内置记忆 Provider — SQLite + FTS5 + 语义向量搜索.

    三路搜索架构:
    - FTS5 全文搜索 (精确匹配)
    - LIKE 模糊搜索 (中文 2-gram 兜底)
    - 语义向量搜索 (embedding 相似度，可选)

    语义搜索渐进增强:
    - 有 sentence-transformers → LocalEmbedder (真语义)
    - 没有 → SimpleHashEmbedder (trigram hash 近似，零依赖)
    - 初始化失败 → 仅 FTS5 + LIKE，不影响核心功能
    """

    def __init__(self, db_path: str = "", enable_semantic: bool = True) -> None:
        self._db_path = db_path
        self._db = None
        self._enable_semantic = enable_semantic
        self._semantic = None  # SemanticSearchEngine (可选)

    async def initialize(self) -> None:
        """初始化数据库."""
        import aiosqlite

        if not self._db_path:
            from agent.core.config import get_memory_dir
            self._db_path = str(get_memory_dir() / "memory.db")

        self._db = await aiosqlite.connect(self._db_path)

        # WAL 模式 — 提升并发读写性能，减少 "database is locked" 错误
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")  # 等待锁最多 5 秒
        await self._db.execute("PRAGMA synchronous=NORMAL")  # WAL 模式下 NORMAL 足够安全

        # 主表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL DEFAULT 'fact',
                importance TEXT NOT NULL DEFAULT 'medium',
                user_id TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                metadata TEXT DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                access_count INTEGER DEFAULT 0
            )
        """)

        # FTS5 全文搜索
        await self._db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                content,
                memory_id UNINDEXED,
                tokenize='unicode61'
            )
        """)

        # 索引
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id, memory_type)
        """)
        await self._db.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_type
            ON memories(memory_type, importance)
        """)

        # 合并历史表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memory_consolidations (
                consolidation_id TEXT PRIMARY KEY,
                source_ids TEXT NOT NULL,
                result_id TEXT NOT NULL,
                strategy TEXT DEFAULT 'merge',
                created_at REAL NOT NULL
            )
        """)

        # 迁移: memories 加 consolidated_from 列
        try:
            cols = [r[1] for r in await self._db.execute_fetchall("PRAGMA table_info(memories)")]
            if "consolidated_from" not in cols:
                await self._db.execute("ALTER TABLE memories ADD COLUMN consolidated_from TEXT DEFAULT '[]'")
            if "usefulness_score" not in cols:
                await self._db.execute("ALTER TABLE memories ADD COLUMN usefulness_score REAL DEFAULT 0.5")
            if "feedback_count" not in cols:
                await self._db.execute("ALTER TABLE memories ADD COLUMN feedback_count INTEGER DEFAULT 0")
        except Exception:
            pass

        # 反馈记录表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS memory_feedback (
                feedback_id TEXT PRIMARY KEY,
                memory_id TEXT NOT NULL,
                signal TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)

        # 自我反思表
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                reflection_id TEXT PRIMARY KEY,
                reflection_type TEXT NOT NULL,
                content TEXT NOT NULL,
                action_items TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
        """)

        await self._db.commit()
        logger.info("BuiltinMemoryProvider initialized: %s", self._db_path)

        # 初始化语义搜索引擎 (可选，失败不影响核心功能)
        if self._enable_semantic:
            try:
                from agent.memory.embedding import SemanticSearchEngine, SimpleHashEmbedder
                embedder = None
                try:
                    from sentence_transformers import SentenceTransformer  # noqa: F401
                    from agent.memory.embedding import LocalEmbedder
                    embedder = LocalEmbedder()
                    logger.info("Semantic search: using LocalEmbedder (sentence-transformers)")
                except ImportError:
                    pass

                if embedder is None:
                    embedder = SimpleHashEmbedder()
                    logger.info("Semantic search: using SimpleHashEmbedder (hash fallback)")

                # 向量 DB 路径与记忆 DB 同目录
                vec_db_path = None
                if self._db_path and self._db_path != ":memory:":
                    import os
                    vec_db_path = os.path.join(os.path.dirname(self._db_path), "vectors.db")

                self._semantic = SemanticSearchEngine(embedder=embedder, db_path=vec_db_path)
                await self._semantic.initialize()

                # 对已有记忆建立索引 (如果向量库为空)
                if self._semantic.size == 0:
                    cursor = await self._db.execute("SELECT memory_id, content FROM memories")
                    batch = []
                    async for row in cursor:
                        batch.append((row[0], row[1], None))
                    if batch:
                        indexed = await self._semantic.index_batch(batch)
                        logger.info("Indexed %d existing memories into semantic engine", indexed)

            except Exception as e:
                logger.debug("Semantic search init failed (non-critical): %s", e)
                self._semantic = None

    async def store(self, memory: Memory, _skip_commit: bool = False) -> str:
        """存储记忆.

        Args:
            memory: 要存储的记忆对象
            _skip_commit: 为 True 时跳过 commit，用于外部事务控制 (如 consolidator)
        """
        import json
        import time
        import uuid

        if not self._db:
            await self.initialize()

        if not memory.memory_id:
            memory.memory_id = str(uuid.uuid4())
        if not memory.created_at:
            memory.created_at = time.time()
        memory.updated_at = time.time()

        await self._db.execute(  # type: ignore
            """
            INSERT OR REPLACE INTO memories
            (memory_id, content, memory_type, importance, user_id,
             tags, metadata, created_at, updated_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory.memory_id,
                memory.content,
                memory.memory_type.value,
                memory.importance.value,
                memory.user_id,
                json.dumps(memory.tags, ensure_ascii=False),
                json.dumps(memory.metadata, ensure_ascii=False),
                memory.created_at,
                memory.updated_at,
                memory.access_count,
            ),
        )

        # 更新 FTS 索引
        await self._db.execute(  # type: ignore
            "INSERT OR REPLACE INTO memories_fts (content, memory_id) VALUES (?, ?)",
            (memory.content, memory.memory_id),
        )

        if not _skip_commit:
            await self._db.commit()  # type: ignore

        # 同步到语义索引
        if self._semantic:
            try:
                await self._semantic.index(memory.memory_id, memory.content, {
                    "user_id": memory.user_id,
                    "memory_type": memory.memory_type.value,
                })
            except Exception as e:
                logger.warning("Semantic index failed for %s: %s", memory.memory_id[:8], e)

        return memory.memory_id

    async def retrieve(self, memory_id: str) -> Optional[Memory]:
        """按 ID 获取记忆."""
        if not self._db:
            return None

        cursor = await self._db.execute(
            "SELECT * FROM memories WHERE memory_id = ?",
            (memory_id,),
        )
        row = await cursor.fetchone()
        if row:
            return self._row_to_memory(row)
        return None

    async def search(
        self,
        query: str,
        user_id: str = "",
        memory_types: Optional[list[MemoryType]] = None,
        limit: int = 10,
        min_importance: Optional[MemoryImportance] = None,
    ) -> list[MemorySearchResult]:
        """搜索记忆 (FTS5 + LIKE 双路搜索).

        FTS5 unicode61 对中文分词不理想，因此同时用 LIKE 兜底，
        合并去重后按相关度排序返回。
        """
        if not self._db:
            return []

        seen_ids: set[str] = set()
        results: list[MemorySearchResult] = []

        def _should_include(memory: Memory) -> bool:
            if user_id and memory.user_id and memory.user_id != user_id:
                return False
            if memory_types and memory.memory_type not in memory_types:
                return False
            if min_importance:
                importance_order = ["low", "medium", "high", "critical"]
                if importance_order.index(memory.importance.value) < importance_order.index(min_importance.value):
                    return False
            return True

        # 路径 1: FTS5 全文搜索
        try:
            fts_query = query.replace('"', '""')
            cursor = await self._db.execute(
                """
                SELECT m.*, rank
                FROM memories_fts fts
                JOIN memories m ON fts.memory_id = m.memory_id
                WHERE memories_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (f'"{fts_query}"', limit * 2),
            )

            async for row in cursor:
                memory = self._row_to_memory(row[:-1])
                rank = row[-1]
                if memory.memory_id in seen_ids or not _should_include(memory):
                    continue
                seen_ids.add(memory.memory_id)
                results.append(MemorySearchResult(
                    memory=memory,
                    relevance_score=min(1.0, abs(rank) / 10.0) if rank else 0.5,
                    match_type="fts",
                ))
        except Exception as e:
            logger.debug("FTS search failed: %s", e)

        # 路径 2: LIKE 模糊搜索 (中文兜底，支持关键词拆分)
        if len(results) < limit:
            try:
                # 拆分查询为关键词，每个词单独 LIKE OR
                # 检测是否含中文字符 (CJK Unified Ideographs)
                has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in query)
                if has_cjk:
                    # 中文按 2-gram 滑窗拆分 (空格分词不适用)
                    keywords = [query[i:i+2] for i in range(0, max(1, len(query) - 1))]
                    keywords = keywords[:5]  # 最多 5 个
                else:
                    keywords = [w for w in query.split() if len(w) >= 2]
                    if not keywords:
                        keywords = [query]

                # 构建 OR 条件
                conditions = ["content LIKE ?"] * len(keywords)
                # 也加反向匹配（新内容包含旧内容）
                conditions.append("? LIKE '%' || content || '%'")
                where = " OR ".join(conditions)
                params = [f"%{kw}%" for kw in keywords] + [query]

                cursor = await self._db.execute(
                    f"""
                    SELECT * FROM memories
                    WHERE {where}
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    params + [limit * 2],
                )
                async for row in cursor:
                    memory = self._row_to_memory(row)
                    if memory.memory_id in seen_ids or not _should_include(memory):
                        continue
                    seen_ids.add(memory.memory_id)
                    results.append(MemorySearchResult(
                        memory=memory,
                        relevance_score=0.6,
                        match_type="like",
                    ))
                    if len(results) >= limit:
                        break
            except Exception as e:
                logger.warning("LIKE search failed: %s", e)

        # 路径 3: 语义向量搜索 (如果可用)
        if self._semantic and len(results) < limit:
            try:
                # min_score 根据 embedder 类型调整
                # SimpleHashEmbedder 分数偏低，用 0.01; 真语义模型用 0.3
                from agent.memory.embedding import SimpleHashEmbedder
                is_hash = isinstance(self._semantic._embedder, SimpleHashEmbedder)
                min_score = 0.01 if is_hash else 0.3

                semantic_results = await self._semantic.search(
                    query, top_k=limit, min_score=min_score,
                )
                for sr in semantic_results:
                    if sr["id"] in seen_ids:
                        continue
                    memory = await self.retrieve(sr["id"])
                    if memory and _should_include(memory):
                        seen_ids.add(sr["id"])
                        results.append(MemorySearchResult(
                            memory=memory,
                            relevance_score=sr["score"],
                            match_type="semantic",
                        ))
                        if len(results) >= limit:
                            break
            except Exception as e:
                logger.debug("Semantic search failed: %s", e)

        # 按相关度排序 (有用性加权)
        for r in results:
            if hasattr(r.memory, 'usefulness_score') and r.memory.feedback_count > 0:
                r.relevance_score *= (0.7 + 0.3 * r.memory.usefulness_score)
        results.sort(key=lambda r: r.relevance_score, reverse=True)
        results = results[:limit]

        # 更新访问计数
        for r in results:
            await self._db.execute(
                "UPDATE memories SET access_count = access_count + 1 WHERE memory_id = ?",
                (r.memory.memory_id,),
            )
        if results:
            await self._db.commit()

        return results

    async def update(self, memory_id: str, updates: dict[str, Any]) -> bool:
        """更新记忆."""
        if not self._db:
            return False

        import time
        if "updated_at" not in updates:
            updates["updated_at"] = time.time()

        set_clauses = []
        values = []
        for key, value in updates.items():
            if key in ("content", "memory_type", "importance", "user_id", "updated_at", "access_count", "usefulness_score", "feedback_count"):
                set_clauses.append(f"{key} = ?")
                values.append(value)
            elif key in ("tags", "metadata"):
                import json
                set_clauses.append(f"{key} = ?")
                values.append(json.dumps(value, ensure_ascii=False))

        if not set_clauses:
            return False

        values.append(memory_id)
        await self._db.execute(
            f"UPDATE memories SET {', '.join(set_clauses)} WHERE memory_id = ?",
            values,
        )

        # 更新 FTS
        if "content" in updates:
            await self._db.execute(
                "INSERT OR REPLACE INTO memories_fts (content, memory_id) VALUES (?, ?)",
                (updates["content"], memory_id),
            )

        await self._db.commit()
        return True

    async def delete(self, memory_id: str) -> bool:
        """删除记忆."""
        if not self._db:
            return False

        await self._db.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
        await self._db.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
        await self._db.commit()

        # 同步移除语义索引
        if self._semantic:
            try:
                await self._semantic.remove(memory_id)
            except Exception:
                pass

        return True

    async def list_memories(
        self,
        user_id: str = "",
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Memory]:
        """列出记忆."""
        if not self._db:
            return []

        conditions = []
        params: list[Any] = []

        if user_id:
            conditions.append("(user_id = ? OR user_id = '')")
            params.append(user_id)
        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type.value)

        where = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        cursor = await self._db.execute(
            f"""
            SELECT * FROM memories
            WHERE {where}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
            """,
            params,
        )

        memories = []
        async for row in cursor:
            memories.append(self._row_to_memory(row))
        return memories

    async def get_stats(self) -> dict[str, Any]:
        """获取统计."""
        if not self._db:
            return {}

        cursor = await self._db.execute(
            "SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type"
        )
        type_counts = {}
        async for row in cursor:
            type_counts[row[0]] = row[1]

        cursor = await self._db.execute("SELECT COUNT(*) FROM memories")
        total = (await cursor.fetchone())[0]

        return {
            "total_memories": total,
            "by_type": type_counts,
            "db_path": self._db_path,
        }

    async def close(self) -> None:
        """关闭数据库."""
        if self._semantic:
            await self._semantic.close()
            self._semantic = None
        if self._db:
            await self._db.close()
            self._db = None

    def _row_to_memory(self, row: tuple) -> Memory:
        """将数据库行转换为 Memory."""
        import json
        m = Memory(
            memory_id=row[0],
            content=row[1],
            memory_type=MemoryType(row[2]),
            importance=MemoryImportance(row[3]),
            user_id=row[4],
            tags=json.loads(row[5]) if row[5] else [],
            metadata=json.loads(row[6]) if row[6] else {},
            created_at=row[7],
            updated_at=row[8],
            access_count=row[9],
        )
        # 新列 (向后兼容: 旧 DB 可能没有这些列)
        if len(row) > 11:
            m.usefulness_score = row[11] if row[11] is not None else 0.5
        if len(row) > 12:
            m.feedback_count = row[12] if row[12] is not None else 0
        return m

# ═══════════════════════════════════════════════════════════════════
#  外部 Memory Provider 实现
# ═══════════════════════════════════════════════════════════════════

class RedisMemoryProvider(BaseMemoryProvider):
    """Redis 记忆 Provider — 高性能缓存 + 搜索.

    依赖: pip install redis[hiredis]
    支持 Redis Stack 的 RediSearch 全文搜索。
    """

    def __init__(self, url: str = "redis://localhost:6379", prefix: str = "xjd:memory:") -> None:
        self._url = url
        self._prefix = prefix
        self._redis = None

    async def initialize(self) -> None:
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._url, decode_responses=True)
            await self._redis.ping()
            logger.info("RedisMemoryProvider initialized: %s", self._url)
        except ImportError:
            raise ImportError("redis 未安装，请运行: pip install redis[hiredis]")

    async def store(self, memory: Memory) -> str:
        import json, time, uuid
        if not memory.memory_id:
            memory.memory_id = str(uuid.uuid4())
        if not memory.created_at:
            memory.created_at = time.time()
        memory.updated_at = time.time()

        key = f"{self._prefix}{memory.memory_id}"
        await self._redis.hset(key, mapping={
            "content": memory.content,
            "memory_type": memory.memory_type.value,
            "importance": memory.importance.value,
            "user_id": memory.user_id,
            "tags": json.dumps(memory.tags),
            "metadata": json.dumps(memory.metadata),
            "created_at": str(memory.created_at),
            "updated_at": str(memory.updated_at),
            "access_count": str(memory.access_count),
        })
        # 索引 key
        await self._redis.sadd(f"{self._prefix}_index", memory.memory_id)
        return memory.memory_id

    async def retrieve(self, memory_id: str) -> Optional[Memory]:
        data = await self._redis.hgetall(f"{self._prefix}{memory_id}")
        if not data:
            return None
        return self._data_to_memory(memory_id, data)

    async def search(self, query: str, user_id: str = "", memory_types=None, limit: int = 10, min_importance=None) -> list[MemorySearchResult]:
        results = []
        ids = await self._redis.smembers(f"{self._prefix}_index")
        query_lower = query.lower()
        for mid in ids:
            data = await self._redis.hgetall(f"{self._prefix}{mid}")
            if not data:
                continue
            content = data.get("content", "")
            if query_lower in content.lower():
                mem = self._data_to_memory(mid, data)
                if user_id and mem.user_id and mem.user_id != user_id:
                    continue
                results.append(MemorySearchResult(memory=mem, relevance_score=0.7, match_type="redis"))
                if len(results) >= limit:
                    break
        return results

    async def update(self, memory_id: str, updates: dict) -> bool:
        import time
        key = f"{self._prefix}{memory_id}"
        if not await self._redis.exists(key):
            return False
        updates["updated_at"] = str(time.time())
        await self._redis.hset(key, mapping={k: str(v) for k, v in updates.items()})
        return True

    async def delete(self, memory_id: str) -> bool:
        key = f"{self._prefix}{memory_id}"
        deleted = await self._redis.delete(key)
        await self._redis.srem(f"{self._prefix}_index", memory_id)
        return deleted > 0

    async def list_memories(self, user_id="", memory_type=None, limit=100, offset=0) -> list[Memory]:
        ids = sorted(await self._redis.smembers(f"{self._prefix}_index"))
        memories = []
        for mid in ids[offset:offset + limit]:
            data = await self._redis.hgetall(f"{self._prefix}{mid}")
            if data:
                memories.append(self._data_to_memory(mid, data))
        return memories

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    def _data_to_memory(self, memory_id: str, data: dict) -> Memory:
        import json
        return Memory(
            memory_id=memory_id, content=data.get("content", ""),
            memory_type=MemoryType(data.get("memory_type", "fact")),
            importance=MemoryImportance(data.get("importance", "medium")),
            user_id=data.get("user_id", ""),
            tags=json.loads(data.get("tags", "[]")),
            metadata=json.loads(data.get("metadata", "{}")),
            created_at=float(data.get("created_at", 0)),
            updated_at=float(data.get("updated_at", 0)),
            access_count=int(data.get("access_count", 0)),
        )

class PostgreSQLMemoryProvider(BaseMemoryProvider):
    """PostgreSQL 记忆 Provider — 生产级持久化.

    依赖: pip install asyncpg
    支持 pg_trgm 模糊搜索 + tsvector 全文搜索。
    """

    def __init__(self, dsn: str = "postgresql://localhost/xjd_agent") -> None:
        self._dsn = dsn
        self._pool = None

    async def initialize(self) -> None:
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS memories (
                        memory_id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        memory_type TEXT NOT NULL DEFAULT 'fact',
                        importance TEXT NOT NULL DEFAULT 'medium',
                        user_id TEXT DEFAULT '',
                        tags JSONB DEFAULT '[]',
                        metadata JSONB DEFAULT '{}',
                        created_at DOUBLE PRECISION NOT NULL,
                        updated_at DOUBLE PRECISION NOT NULL,
                        access_count INTEGER DEFAULT 0
                    )
                """)
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_user ON memories(user_id, memory_type)")
            logger.info("PostgreSQLMemoryProvider initialized: %s", self._dsn)
        except ImportError:
            raise ImportError("asyncpg 未安装，请运行: pip install asyncpg")

    async def store(self, memory: Memory) -> str:
        import json, time, uuid
        if not memory.memory_id:
            memory.memory_id = str(uuid.uuid4())
        if not memory.created_at:
            memory.created_at = time.time()
        memory.updated_at = time.time()

        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO memories (memory_id, content, memory_type, importance, user_id, tags, metadata, created_at, updated_at, access_count)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (memory_id) DO UPDATE SET content=$2, updated_at=$9
            """, memory.memory_id, memory.content, memory.memory_type.value, memory.importance.value,
                memory.user_id, json.dumps(memory.tags), json.dumps(memory.metadata),
                memory.created_at, memory.updated_at, memory.access_count)
        return memory.memory_id

    async def retrieve(self, memory_id: str) -> Optional[Memory]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM memories WHERE memory_id=$1", memory_id)
            return self._row_to_memory(row) if row else None

    async def search(self, query: str, user_id="", memory_types=None, limit=10, min_importance=None) -> list[MemorySearchResult]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM memories WHERE content ILIKE $1 ORDER BY updated_at DESC LIMIT $2",
                f"%{query}%", limit,
            )
            return [MemorySearchResult(memory=self._row_to_memory(r), relevance_score=0.7, match_type="pg") for r in rows]

    async def update(self, memory_id: str, updates: dict) -> bool:
        import time
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE memories SET content=$1, updated_at=$2 WHERE memory_id=$3",
                updates.get("content", ""), time.time(), memory_id)
        return True

    async def delete(self, memory_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM memories WHERE memory_id=$1", memory_id)
        return "DELETE 1" in result

    async def list_memories(self, user_id="", memory_type=None, limit=100, offset=0) -> list[Memory]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM memories ORDER BY updated_at DESC LIMIT $1 OFFSET $2", limit, offset)
            return [self._row_to_memory(r) for r in rows]

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def _row_to_memory(self, row) -> Memory:
        import json
        return Memory(
            memory_id=row["memory_id"], content=row["content"],
            memory_type=MemoryType(row["memory_type"]),
            importance=MemoryImportance(row["importance"]),
            user_id=row["user_id"],
            tags=json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"],
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            access_count=row["access_count"],
        )

class ChromaDBMemoryProvider(BaseMemoryProvider):
    """ChromaDB 记忆 Provider — 向量语义搜索.

    依赖: pip install chromadb
    原生向量搜索，适合语义记忆检索。
    """

    def __init__(self, persist_dir: str = "", collection_name: str = "xjd_memories") -> None:
        self._persist_dir = persist_dir
        self._collection_name = collection_name
        self._client = None
        self._collection = None

    async def initialize(self) -> None:
        try:
            import chromadb
            if self._persist_dir:
                self._client = chromadb.PersistentClient(path=self._persist_dir)
            else:
                self._client = chromadb.Client()
            self._collection = self._client.get_or_create_collection(self._collection_name)
            logger.info("ChromaDBMemoryProvider initialized: %s", self._collection_name)
        except ImportError:
            raise ImportError("chromadb 未安装，请运行: pip install chromadb")

    async def store(self, memory: Memory) -> str:
        import json, time, uuid
        if not memory.memory_id:
            memory.memory_id = str(uuid.uuid4())
        if not memory.created_at:
            memory.created_at = time.time()
        memory.updated_at = time.time()

        self._collection.upsert(
            ids=[memory.memory_id],
            documents=[memory.content],
            metadatas=[{
                "memory_type": memory.memory_type.value,
                "importance": memory.importance.value,
                "user_id": memory.user_id,
                "tags": json.dumps(memory.tags),
                "created_at": memory.created_at,
                "updated_at": memory.updated_at,
            }],
        )
        return memory.memory_id

    async def retrieve(self, memory_id: str) -> Optional[Memory]:
        result = self._collection.get(ids=[memory_id])
        if result["ids"]:
            return self._result_to_memory(result, 0)
        return None

    async def search(self, query: str, user_id="", memory_types=None, limit=10, min_importance=None) -> list[MemorySearchResult]:
        result = self._collection.query(query_texts=[query], n_results=limit)
        results = []
        for i in range(len(result["ids"][0])):
            mem = self._result_to_memory_from_query(result, i)
            dist = result["distances"][0][i] if result.get("distances") else 0
            score = max(0, 1 - dist)
            results.append(MemorySearchResult(memory=mem, relevance_score=score, match_type="chroma"))
        return results

    async def update(self, memory_id: str, updates: dict) -> bool:
        if "content" in updates:
            self._collection.update(ids=[memory_id], documents=[updates["content"]])
        return True

    async def delete(self, memory_id: str) -> bool:
        try:
            self._collection.delete(ids=[memory_id])
            return True
        except Exception:
            return False

    async def list_memories(self, user_id="", memory_type=None, limit=100, offset=0) -> list[Memory]:
        result = self._collection.get(limit=limit, offset=offset)
        return [self._result_to_memory(result, i) for i in range(len(result["ids"]))]

    def _result_to_memory(self, result, idx) -> Memory:
        import json
        meta = result["metadatas"][idx] if result["metadatas"] else {}
        return Memory(
            memory_id=result["ids"][idx],
            content=result["documents"][idx] if result["documents"] else "",
            memory_type=MemoryType(meta.get("memory_type", "fact")),
            importance=MemoryImportance(meta.get("importance", "medium")),
            user_id=meta.get("user_id", ""),
            tags=json.loads(meta.get("tags", "[]")),
            created_at=meta.get("created_at", 0),
            updated_at=meta.get("updated_at", 0),
        )

    def _result_to_memory_from_query(self, result, idx) -> Memory:
        import json
        meta = result["metadatas"][0][idx] if result["metadatas"] else {}
        return Memory(
            memory_id=result["ids"][0][idx],
            content=result["documents"][0][idx] if result["documents"] else "",
            memory_type=MemoryType(meta.get("memory_type", "fact")),
            importance=MemoryImportance(meta.get("importance", "medium")),
            user_id=meta.get("user_id", ""),
            tags=json.loads(meta.get("tags", "[]")),
            created_at=meta.get("created_at", 0),
            updated_at=meta.get("updated_at", 0),
        )

# ── Provider 工厂 ──────────────────────────────────────────────

PROVIDER_REGISTRY: dict[str, type[BaseMemoryProvider]] = {
    "sqlite": BuiltinMemoryProvider,
    "redis": RedisMemoryProvider,
    "postgresql": PostgreSQLMemoryProvider,
    "chromadb": ChromaDBMemoryProvider,
}

def create_memory_provider(provider_type: str = "sqlite", **kwargs) -> BaseMemoryProvider:
    """工厂函数 — 根据类型创建 Memory Provider."""
    cls = PROVIDER_REGISTRY.get(provider_type)
    if not cls:
        raise ValueError(f"未知 Memory Provider: {provider_type}。支持: {list(PROVIDER_REGISTRY.keys())}")
    return cls(**kwargs)
