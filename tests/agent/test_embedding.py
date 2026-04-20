"""Tests for agent.memory.embedding — 向量搜索引擎."""

import pytest
import math

from agent.memory.embedding import (
    cosine_similarity,
    vector_to_bytes,
    bytes_to_vector,
    EmbeddingResult,
    VectorIndex,
    BaseEmbedder,
    SemanticSearchEngine,
)


class TestCosineSimilarity:
    def test_identical(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_empty(self):
        assert cosine_similarity([], []) == 0.0

    def test_different_length(self):
        assert cosine_similarity([1.0], [1.0, 2.0]) == 0.0


class TestVectorSerialization:
    def test_roundtrip(self):
        vec = [0.1, 0.2, 0.3, -0.5, 1.0]
        data = vector_to_bytes(vec)
        restored = bytes_to_vector(data)
        for a, b in zip(vec, restored):
            assert a == pytest.approx(b, abs=1e-6)


class TestEmbeddingResult:
    def test_defaults(self):
        r = EmbeddingResult()
        assert r.text == ""
        assert r.vector == []
        assert r.dimensions == 0

    def test_with_vector(self):
        r = EmbeddingResult(text="test", vector=[0.1, 0.2, 0.3])
        assert r.dimensions == 3


class TestVectorIndex:
    @pytest.mark.asyncio
    async def test_add_and_search(self, tmp_path):
        db = str(tmp_path / "vectors.db")
        idx = VectorIndex(db_path=db)
        await idx.initialize()

        await idx.add("v1", [1.0, 0.0, 0.0], {"type": "fact"})
        await idx.add("v2", [0.0, 1.0, 0.0], {"type": "skill"})
        await idx.add("v3", [0.9, 0.1, 0.0], {"type": "fact"})

        assert idx.size == 3

        results = await idx.search([1.0, 0.0, 0.0], top_k=2)
        assert len(results) == 2
        assert results[0][0] == "v1"  # most similar
        assert results[0][1] == pytest.approx(1.0)

        await idx.close()

    @pytest.mark.asyncio
    async def test_filter_metadata(self, tmp_path):
        db = str(tmp_path / "vectors.db")
        idx = VectorIndex(db_path=db)
        await idx.initialize()

        await idx.add("v1", [1.0, 0.0], {"type": "fact"})
        await idx.add("v2", [0.9, 0.1], {"type": "skill"})

        results = await idx.search([1.0, 0.0], filter_metadata={"type": "skill"})
        assert len(results) == 1
        assert results[0][0] == "v2"

        await idx.close()

    @pytest.mark.asyncio
    async def test_remove(self, tmp_path):
        db = str(tmp_path / "vectors.db")
        idx = VectorIndex(db_path=db)
        await idx.initialize()

        await idx.add("v1", [1.0, 0.0])
        assert idx.size == 1
        assert await idx.remove("v1") is True
        assert idx.size == 0
        assert await idx.remove("nonexist") is False

        await idx.close()

    @pytest.mark.asyncio
    async def test_persistence(self, tmp_path):
        db = str(tmp_path / "vectors.db")

        idx1 = VectorIndex(db_path=db)
        await idx1.initialize()
        await idx1.add("v1", [0.5, 0.5], {"note": "test"})
        await idx1.close()

        idx2 = VectorIndex(db_path=db)
        await idx2.initialize()
        assert idx2.size == 1
        results = await idx2.search([0.5, 0.5])
        assert len(results) == 1
        assert results[0][0] == "v1"
        await idx2.close()


class MockEmbedder(BaseEmbedder):
    """Simple hash-based embedder for testing."""

    async def embed(self, text: str) -> list[float]:
        # Produce a deterministic 4-dim vector from text
        h = hash(text) & 0xFFFFFFFF
        return [
            ((h >> 0) & 0xFF) / 255.0,
            ((h >> 8) & 0xFF) / 255.0,
            ((h >> 16) & 0xFF) / 255.0,
            ((h >> 24) & 0xFF) / 255.0,
        ]


class TestSemanticSearchEngine:
    @pytest.mark.asyncio
    async def test_index_and_search(self, tmp_path):
        db = str(tmp_path / "semantic.db")
        engine = SemanticSearchEngine(
            embedder=MockEmbedder(),
            db_path=db,
        )
        await engine.initialize()

        await engine.index("d1", "Python 编程语言")
        await engine.index("d2", "Java 编程语言")
        await engine.index("d3", "烹饪食谱")

        assert engine.size == 3

        results = await engine.search("Python 编程语言", min_score=0.0)
        assert len(results) > 0
        assert results[0]["id"] == "d1"
        assert results[0]["score"] == pytest.approx(1.0)

        await engine.close()

    @pytest.mark.asyncio
    async def test_remove(self, tmp_path):
        db = str(tmp_path / "semantic.db")
        engine = SemanticSearchEngine(embedder=MockEmbedder(), db_path=db)
        await engine.initialize()

        await engine.index("d1", "test")
        assert engine.size == 1
        assert await engine.remove("d1") is True
        assert engine.size == 0

        await engine.close()
