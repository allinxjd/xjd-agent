"""示例插件: 知识库问答.

基于本地文件构建知识库，支持:
- 加载文件/目录
- 分块索引
- 语义检索增强 (RAG)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agent.plugins.manager import BasePlugin

logger = logging.getLogger(__name__)

class KnowledgeBasePlugin(BasePlugin):
    """知识库问答插件 — RAG (Retrieval-Augmented Generation)."""

    def __init__(self) -> None:
        super().__init__()
        self._documents: dict[str, str] = {}  # doc_id → content
        self._chunks: list[dict] = []  # {id, text, doc_id, metadata}

    async def on_enable(self) -> None:
        # 加载配置的文档目录
        docs_dir = self.config.get("docs_dir", "")
        if docs_dir and Path(docs_dir).exists():
            await self.load_directory(docs_dir)

    def get_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "kb_search",
                "description": "从知识库中搜索相关文档片段",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "返回结果数量",
                            "default": 3,
                        },
                    },
                    "required": ["query"],
                },
                "handler": self._search,
            },
            {
                "name": "kb_load",
                "description": "加载文件到知识库",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "文件路径",
                        },
                    },
                    "required": ["path"],
                },
                "handler": self._load_file,
            },
            {
                "name": "kb_stats",
                "description": "查看知识库统计信息",
                "parameters": {"type": "object", "properties": {}},
                "handler": self._stats,
            },
        ]

    async def load_directory(self, dir_path: str) -> int:
        """加载目录下的所有文本文件."""
        path = Path(dir_path)
        if not path.exists():
            return 0

        count = 0
        for file in path.rglob("*"):
            if file.suffix in (".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml"):
                try:
                    content = file.read_text(encoding="utf-8", errors="ignore")
                    doc_id = str(file.relative_to(path))
                    self._documents[doc_id] = content
                    self._index_document(doc_id, content)
                    count += 1
                except Exception as e:
                    logger.warning("Failed to load %s: %s", file, e)

        logger.info("KnowledgeBase loaded %d documents from %s", count, dir_path)
        return count

    def _index_document(self, doc_id: str, content: str) -> None:
        """将文档分块索引."""
        chunk_size = self.config.get("chunk_size", 500)
        overlap = self.config.get("chunk_overlap", 50)

        # 按段落分割
        paragraphs = content.split("\n\n")
        current_chunk = ""
        chunk_idx = 0

        for para in paragraphs:
            if len(current_chunk) + len(para) > chunk_size and current_chunk:
                self._chunks.append({
                    "id": f"{doc_id}::{chunk_idx}",
                    "text": current_chunk.strip(),
                    "doc_id": doc_id,
                    "metadata": {"chunk_idx": chunk_idx},
                })
                chunk_idx += 1
                # 保留 overlap
                current_chunk = current_chunk[-overlap:] if overlap else ""

            current_chunk += para + "\n\n"

        # 最后一块
        if current_chunk.strip():
            self._chunks.append({
                "id": f"{doc_id}::{chunk_idx}",
                "text": current_chunk.strip(),
                "doc_id": doc_id,
                "metadata": {"chunk_idx": chunk_idx},
            })

    async def _search(self, query: str, top_k: int = 3) -> str:
        """搜索知识库 (基于关键词匹配的简单实现)."""
        if not self._chunks:
            return "知识库为空。请先使用 kb_load 加载文档。"

        # 简单 TF-IDF 风格匹配
        query_terms = set(query.lower().split())
        scored = []

        for chunk in self._chunks:
            text_lower = chunk["text"].lower()
            score = sum(1 for term in query_terms if term in text_lower)
            # 对中文按字匹配
            char_score = sum(1 for c in query if c in text_lower)
            total_score = score + char_score * 0.1

            if total_score > 0:
                scored.append((total_score, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = scored[:top_k]

        if not results:
            return "未找到相关内容。"

        output = []
        for i, (score, chunk) in enumerate(results, 1):
            text = chunk["text"][:300]
            output.append(f"[{i}] (来源: {chunk['doc_id']})\n{text}...")

        return "\n\n".join(output)

    async def _load_file(self, path: str) -> str:
        """加载单个文件."""
        p = Path(path)
        if not p.exists():
            return f"文件不存在: {path}"

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            doc_id = p.name
            self._documents[doc_id] = content
            self._index_document(doc_id, content)
            return f"已加载: {doc_id} ({len(content)} 字符, {len([c for c in self._chunks if c['doc_id'] == doc_id])} 个分块)"
        except Exception as e:
            return f"加载失败: {e}"

    async def _stats(self) -> str:
        """知识库统计."""
        total_chars = sum(len(c) for c in self._documents.values())
        return (
            f"📚 知识库统计:\n"
            f"  文档数: {len(self._documents)}\n"
            f"  分块数: {len(self._chunks)}\n"
            f"  总字符: {total_chars:,}"
        )
