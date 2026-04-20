"""RAG Pipeline — 检索增强生成.

完整的 RAG 管线:
  文档加载 → 分块 → 嵌入 → 向量存储 → 检索 → 上下文注入

支持:
- 多种文档格式: txt, md, pdf, py, json, yaml, csv, html
- 可配置分块策略 (固定大小 / 按段落 / 按语义)
- 向量存储: 内存 / SQLite (复用 embedding.py)
- Top-K 检索 + 相关性阈值过滤
- 上下文注入到 Agent 对话

"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

class ChunkStrategy(str, Enum):
    """分块策略."""

    FIXED = "fixed"          # 固定字符数
    PARAGRAPH = "paragraph"  # 按段落
    SENTENCE = "sentence"    # 按句子

@dataclass
class Document:
    """文档."""

    content: str = ""
    source: str = ""  # 文件路径或 URL
    metadata: dict = field(default_factory=dict)

@dataclass
class Chunk:
    """文档分块."""

    text: str = ""
    source: str = ""
    chunk_index: int = 0
    metadata: dict = field(default_factory=dict)

@dataclass
class RetrievalResult:
    """检索结果."""

    chunk: Chunk
    score: float = 0.0

class DocumentLoader:
    """文档加载器 — 支持多种格式."""

    SUPPORTED_EXTENSIONS = {".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml", ".csv", ".html", ".htm", ".xml", ".toml", ".cfg", ".ini", ".sh", ".sql", ".go", ".rs", ".java", ".c", ".cpp", ".h"}

    @staticmethod
    async def load_file(path: str | Path) -> Document:
        """加载单个文件."""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        try:
            content = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = p.read_text(encoding="latin-1")

        return Document(content=content, source=str(p), metadata={"filename": p.name, "extension": p.suffix, "size": p.stat().st_size})

    @staticmethod
    async def load_directory(directory: str | Path, recursive: bool = True, extensions: Optional[set[str]] = None) -> list[Document]:
        """加载目录下所有文件."""
        exts = extensions or DocumentLoader.SUPPORTED_EXTENSIONS
        docs = []
        d = Path(directory)

        pattern = "**/*" if recursive else "*"
        for p in d.glob(pattern):
            if p.is_file() and p.suffix in exts:
                try:
                    doc = await DocumentLoader.load_file(p)
                    docs.append(doc)
                except Exception as e:
                    logger.warning("加载 %s 失败: %s", p, e)

        return docs

class TextChunker:
    """文本分块器."""

    def __init__(self, strategy: ChunkStrategy = ChunkStrategy.FIXED, chunk_size: int = 500, overlap: int = 50) -> None:
        self._strategy = strategy
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk(self, doc: Document) -> list[Chunk]:
        """将文档分块."""
        if self._strategy == ChunkStrategy.FIXED:
            return self._chunk_fixed(doc)
        elif self._strategy == ChunkStrategy.PARAGRAPH:
            return self._chunk_paragraph(doc)
        elif self._strategy == ChunkStrategy.SENTENCE:
            return self._chunk_sentence(doc)
        return self._chunk_fixed(doc)

    def _chunk_fixed(self, doc: Document) -> list[Chunk]:
        """固定大小分块."""
        text = doc.content
        chunks = []
        start = 0
        idx = 0
        while start < len(text):
            end = start + self._chunk_size
            chunk_text = text[start:end]
            if chunk_text.strip():
                chunks.append(Chunk(text=chunk_text, source=doc.source, chunk_index=idx, metadata=doc.metadata))
                idx += 1
            start = end - self._overlap
        return chunks

    def _chunk_paragraph(self, doc: Document) -> list[Chunk]:
        """按段落分块."""
        paragraphs = doc.content.split("\n\n")
        chunks = []
        current = ""
        idx = 0
        for para in paragraphs:
            if len(current) + len(para) > self._chunk_size and current:
                chunks.append(Chunk(text=current.strip(), source=doc.source, chunk_index=idx, metadata=doc.metadata))
                idx += 1
                current = ""
            current += para + "\n\n"
        if current.strip():
            chunks.append(Chunk(text=current.strip(), source=doc.source, chunk_index=idx, metadata=doc.metadata))
        return chunks

    def _chunk_sentence(self, doc: Document) -> list[Chunk]:
        """按句子分块."""
        import re
        sentences = re.split(r'(?<=[。！？.!?])\s*', doc.content)
        chunks = []
        current = ""
        idx = 0
        for sent in sentences:
            if len(current) + len(sent) > self._chunk_size and current:
                chunks.append(Chunk(text=current.strip(), source=doc.source, chunk_index=idx, metadata=doc.metadata))
                idx += 1
                current = ""
            current += sent + " "
        if current.strip():
            chunks.append(Chunk(text=current.strip(), source=doc.source, chunk_index=idx, metadata=doc.metadata))
        return chunks

class RAGPipeline:
    """RAG 管线 — 文档加载 → 分块 → 嵌入 → 检索 → 注入."""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        chunk_strategy: ChunkStrategy = ChunkStrategy.PARAGRAPH,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        top_k: int = 5,
        score_threshold: float = 0.3,
    ) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".xjd-agent" / "rag"
        self._data_dir = Path(data_dir)
        self._chunker = TextChunker(strategy=chunk_strategy, chunk_size=chunk_size, overlap=chunk_overlap)
        self._top_k = top_k
        self._score_threshold = score_threshold
        self._chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []
        self._initialized = False
        self._embed_fn = None

    async def initialize(self, embed_fn=None) -> None:
        """初始化 RAG 管线.

        自动检测可用的嵌入引擎 (优先级: 自定义 > OpenAI > SentenceTransformers > Hash fallback).
        """
        self._data_dir.mkdir(parents=True, exist_ok=True)
        if embed_fn:
            self._embed_fn = embed_fn
        else:
            self._embed_fn = await self._auto_detect_embedder()
        self._initialized = True

    async def ingest_file(self, path: str | Path) -> int:
        """导入单个文件，返回分块数."""
        doc = await DocumentLoader.load_file(path)
        return await self._ingest_document(doc)

    async def ingest_directory(self, directory: str | Path, recursive: bool = True) -> int:
        """导入整个目录，返回总分块数."""
        docs = await DocumentLoader.load_directory(directory, recursive=recursive)
        total = 0
        for doc in docs:
            total += await self._ingest_document(doc)
        return total

    async def ingest_text(self, text: str, source: str = "manual") -> int:
        """导入纯文本."""
        doc = Document(content=text, source=source)
        return await self._ingest_document(doc)

    async def _ingest_document(self, doc: Document) -> int:
        """处理单个文档: 分块 → 嵌入 → 存储."""
        chunks = self._chunker.chunk(doc)
        for chunk in chunks:
            vector = await self._embed_fn(chunk.text)
            self._chunks.append(chunk)
            self._vectors.append(vector)
        logger.info("导入 %s: %d 个分块", doc.source, len(chunks))
        return len(chunks)

    async def retrieve(self, query: str, top_k: Optional[int] = None) -> list[RetrievalResult]:
        """检索与查询最相关的分块."""
        if not self._chunks:
            return []

        k = top_k or self._top_k
        query_vec = await self._embed_fn(query)

        # 计算相似度
        from agent.memory.embedding import cosine_similarity
        scored = []
        for i, vec in enumerate(self._vectors):
            score = cosine_similarity(query_vec, vec)
            if score >= self._score_threshold:
                scored.append((i, score))

        # 排序取 top-k
        scored.sort(key=lambda x: x[1], reverse=True)
        results = []
        for idx, score in scored[:k]:
            results.append(RetrievalResult(chunk=self._chunks[idx], score=score))
        return results

    async def query(self, question: str, top_k: Optional[int] = None) -> str:
        """检索并格式化为上下文字符串."""
        results = await self.retrieve(question, top_k)
        if not results:
            return ""

        parts = ["--- 检索到的相关内容 ---"]
        for i, r in enumerate(results, 1):
            parts.append(f"\n[{i}] (来源: {r.chunk.source}, 相关度: {r.score:.2f})")
            parts.append(r.chunk.text)
        return "\n".join(parts)

    def get_stats(self) -> dict:
        """获取统计信息."""
        sources = set(c.source for c in self._chunks)
        return {
            "total_chunks": len(self._chunks),
            "total_sources": len(sources),
            "sources": list(sources)[:20],
        }

    def clear(self) -> None:
        """清空所有数据."""
        self._chunks.clear()
        self._vectors.clear()

    @staticmethod
    async def _auto_detect_embedder():
        """自动检测最佳可用嵌入引擎，返回 embed 函数."""
        import os
        from agent.memory.embedding import OpenAIEmbedder, LocalEmbedder, SimpleHashEmbedder

        # 1. OpenAI API key 可用 → 使用 OpenAI Embeddings
        api_key = os.environ.get("OPENAI_API_KEY", "")
        base_url = os.environ.get("OPENAI_BASE_URL", "")
        if api_key:
            embedder = OpenAIEmbedder(api_key=api_key, base_url=base_url or "https://api.openai.com/v1")
            logger.info("RAG: using OpenAI embedder")
            return embedder.embed

        # 2. sentence-transformers 已安装 → 使用本地模型
        try:
            import sentence_transformers  # noqa: F401
            embedder = LocalEmbedder()
            logger.info("RAG: using local SentenceTransformers embedder")
            return embedder.embed
        except ImportError:
            pass

        # 3. Fallback → hash-based embedder (开发/测试用)
        embedder = SimpleHashEmbedder(dimensions=256)
        logger.info("RAG: using hash embedder (fallback, no semantic understanding)")
        return embedder.embed
