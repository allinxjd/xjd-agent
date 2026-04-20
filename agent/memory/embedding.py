"""Embedding 语义搜索引擎 — 为记忆系统提供向量检索能力.

支持:
- OpenAI Embeddings (text-embedding-3-small)
- 本地 SentenceTransformers
- 内存向量索引 (余弦相似度)
- SQLite 向量持久化
- 混合搜索 (FTS5 文本 + 向量语义)
"""

from __future__ import annotations

import json
import abc
import logging
import math
import struct

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class EmbeddingResult:
    """嵌入结果."""

    text: str = ""
    vector: list[float] = None  # type: ignore
    model: str = ""
    dimensions: int = 0

    def __post_init__(self):
        if self.vector is None:
            self.vector = []
        self.dimensions = len(self.vector)

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算余弦相似度."""
    if len(a) != len(b) or not a:
        return 0.0

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)

def vector_to_bytes(vec: list[float]) -> bytes:
    """向量序列化为 bytes (用于 SQLite 存储)."""
    return struct.pack(f"{len(vec)}f", *vec)

def bytes_to_vector(data: bytes) -> list[float]:
    """从 bytes 反序列化向量."""
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))

class BaseEmbedder(abc.ABC):
    """嵌入引擎基类."""

    @abc.abstractmethod
    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        results = []
        for t in texts:
            results.append(await self.embed(t))
        return results

class OpenAIEmbedder(BaseEmbedder):
    """OpenAI Embedding API."""

    def __init__(
        self,
        api_key: str = "",
        model: str = "text-embedding-3-small",
        base_url: str = "",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url or "https://api.openai.com/v1"

    async def embed(self, text: str) -> list[float]:
        try:
            import httpx

            url = f"{self._base_url}/embeddings"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"input": text, "model": self._model},
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]

        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
            logger.error("OpenAI embedding error: %s", e)
            return []

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        try:
            import httpx

            url = f"{self._base_url}/embeddings"
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={"input": texts, "model": self._model},
                )
                resp.raise_for_status()
                data = resp.json()
                # 按 index 排序
                items = sorted(data["data"], key=lambda x: x["index"])
                return [item["embedding"] for item in items]

        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
            logger.error("OpenAI batch embedding error: %s", e)
            return [[] for _ in texts]

class LocalEmbedder(BaseEmbedder):
    """本地 SentenceTransformers 嵌入."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
            except ImportError:
                raise ImportError("sentence-transformers 未安装。请运行: pip install sentence-transformers")
        return self._model

    async def embed(self, text: str) -> list[float]:
        import asyncio
        model = self._ensure_model()
        loop = asyncio.get_event_loop()
        vec = await loop.run_in_executor(None, lambda: model.encode(text).tolist())
        return vec

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        import asyncio
        model = self._ensure_model()
        loop = asyncio.get_event_loop()
        vecs = await loop.run_in_executor(None, lambda: model.encode(texts).tolist())
        return vecs

class SimpleHashEmbedder(BaseEmbedder):
    """基于哈希的简易嵌入器 — 无外部依赖, 用于测试和开发.

    使用字符级 n-gram 哈希生成固定维度的伪向量。
    不具备真正的语义理解能力, 但相同文本一定产生相同向量。
    """

    def __init__(self, dimensions: int = 64) -> None:
        self._dimensions = dimensions

    async def embed(self, text: str) -> list[float]:
        import hashlib

        text = text.strip().lower()
        if not text:
            return [0.0] * self._dimensions

        vec = [0.0] * self._dimensions

        # 字符级 trigram 哈希
        for i in range(len(text)):
            for n in (1, 2, 3):
                gram = text[i:i + n]
                h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
                idx = h % self._dimensions
                val = ((h >> 8) % 1000) / 1000.0 - 0.5
                vec[idx] += val

        # L2 归一化
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]

        return vec

class VectorIndex:
    """内存向量索引 + SQLite 持久化.

    用法:
        index = VectorIndex(db_path="~/.xjd-agent/vectors.db")
        await index.initialize()

        await index.add("mem_1", [0.1, 0.2, ...], metadata={"type": "fact"})
        results = await index.search([0.1, 0.2, ...], top_k=5)
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path:
            self._db_path = db_path
        else:
            from agent.core.config import get_home
            self._db_path = str(get_home() / "vectors.db")

        self._vectors: dict[str, list[float]] = {}  # id → vector
        self._metadata: dict[str, dict] = {}         # id → metadata
        self._db = None

    async def initialize(self) -> None:
        """初始化索引 (加载已有数据)."""
        import aiosqlite

        self._db = await aiosqlite.connect(self._db_path)
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS vectors (
                id TEXT PRIMARY KEY,
                vector BLOB NOT NULL,
                metadata TEXT DEFAULT '{}'
            )
        """)
        await self._db.commit()

        # 加载到内存
        cursor = await self._db.execute("SELECT id, vector, metadata FROM vectors")
        count = 0
        async for row in cursor:
            vid, vec_bytes, meta_str = row
            self._vectors[vid] = bytes_to_vector(vec_bytes)
            try:
                self._metadata[vid] = json.loads(meta_str) if meta_str else {}
            except json.JSONDecodeError:
                self._metadata[vid] = {}
            count += 1

        logger.info("VectorIndex loaded: %d vectors", count)

    async def add(
        self,
        id: str,
        vector: list[float],
        metadata: Optional[dict] = None,
    ) -> None:
        """添加向量."""
        self._vectors[id] = vector
        self._metadata[id] = metadata or {}

        if self._db:
            vec_bytes = vector_to_bytes(vector)
            meta_str = json.dumps(metadata or {}, ensure_ascii=False)
            await self._db.execute(
                "INSERT OR REPLACE INTO vectors (id, vector, metadata) VALUES (?, ?, ?)",
                (id, vec_bytes, meta_str),
            )
            await self._db.commit()

    async def remove(self, id: str) -> bool:
        """移除向量."""
        if id in self._vectors:
            del self._vectors[id]
            self._metadata.pop(id, None)
            if self._db:
                await self._db.execute("DELETE FROM vectors WHERE id = ?", (id,))
                await self._db.commit()
            return True
        return False

    async def search(
        self,
        query_vector: list[float],
        top_k: int = 10,
        min_score: float = 0.0,
        filter_metadata: Optional[dict] = None,
    ) -> list[tuple[str, float, dict]]:
        """搜索最相似的向量.

        Returns:
            [(id, similarity_score, metadata), ...]
        """
        if not query_vector or not self._vectors:
            return []

        results = []
        for vid, vec in self._vectors.items():
            # 元数据过滤
            if filter_metadata:
                meta = self._metadata.get(vid, {})
                match = all(meta.get(k) == v for k, v in filter_metadata.items())
                if not match:
                    continue

            score = cosine_similarity(query_vector, vec)
            if score >= min_score:
                results.append((vid, score, self._metadata.get(vid, {})))

        # 按相似度降序
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    @property
    def size(self) -> int:
        return len(self._vectors)

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

class SemanticSearchEngine:
    """语义搜索引擎 — 集成嵌入模型 + 向量索引.

    用法:
        engine = SemanticSearchEngine(
            embedder=OpenAIEmbedder(api_key="sk-..."),
        )
        await engine.initialize()

        # 索引文档
        await engine.index("doc_1", "用户喜欢 Python 编程")

        # 语义搜索
        results = await engine.search("Python 开发", top_k=5)
    """

    def __init__(
        self,
        embedder: Optional[BaseEmbedder] = None,
        index: Optional[VectorIndex] = None,
        db_path: Optional[str] = None,
    ) -> None:
        self._embedder = embedder or LocalEmbedder()
        self._index = index or VectorIndex(db_path=db_path)

    async def initialize(self) -> None:
        await self._index.initialize()

    async def index(
        self,
        id: str,
        text: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """索引文本."""
        vector = await self._embedder.embed(text)
        if vector:
            meta = metadata or {}
            meta["_text"] = text[:500]
            await self._index.add(id, vector, meta)

    async def index_batch(
        self,
        items: list[tuple[str, str, Optional[dict]]],
    ) -> int:
        """批量索引. items = [(id, text, metadata), ...]."""
        texts = [item[1] for item in items]
        vectors = await self._embedder.embed_batch(texts)

        count = 0
        for (id, text, metadata), vector in zip(items, vectors):
            if vector:
                meta = metadata or {}
                meta["_text"] = text[:500]
                await self._index.add(id, vector, meta)
                count += 1

        return count

    async def search(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.3,
        filter_metadata: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        """语义搜索.

        Returns:
            [{"id": ..., "score": ..., "text": ..., "metadata": ...}, ...]
        """
        query_vector = await self._embedder.embed(query)
        if not query_vector:
            return []

        results = await self._index.search(
            query_vector,
            top_k=top_k,
            min_score=min_score,
            filter_metadata=filter_metadata,
        )

        return [
            {
                "id": vid,
                "score": round(score, 4),
                "text": meta.get("_text", ""),
                "metadata": {k: v for k, v in meta.items() if not k.startswith("_")},
            }
            for vid, score, meta in results
        ]

    async def remove(self, id: str) -> bool:
        return await self._index.remove(id)

    @property
    def size(self) -> int:
        return self._index.size

    async def close(self) -> None:
        await self._index.close()
