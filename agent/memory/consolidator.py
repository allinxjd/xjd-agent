"""记忆合并器 — 定期合并语义相似的记忆，减少冗余。

流程:
1. find_similar_clusters() — 用语义搜索找相似记忆聚类
2. consolidate_cluster() — LLM 合并 N 条为 1 条
3. run_consolidation() — 完整管线: 找聚类 → 合并 → 删旧存新
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from agent.memory.provider import Memory, MemoryImportance, MemoryType

logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT = """以下是 {n} 条语义相似的记忆，请合并为一条简洁完整的记忆。
保留所有关键信息，去除重复。如果有矛盾，以最新的（序号越大越新）为准。

{memories_text}

返回 JSON (仅返回 JSON，不要其他内容):
{{"content": "合并后的记忆内容", "memory_type": "fact|preference|context|episodic", "importance": "low|medium|high|critical", "tags": ["tag1"]}}"""


class MemoryConsolidator:
    """记忆合并器."""

    def __init__(self, provider, model_router=None, config=None):
        from agent.memory.config import MemoryConfig
        self._provider = provider
        self._model_router = model_router
        self._config = config or MemoryConfig()

    async def find_similar_clusters(
        self,
        user_id: str = "",
        threshold: Optional[float] = None,
        max_clusters: Optional[int] = None,
    ) -> list[list[Memory]]:
        """用语义搜索找相似记忆聚类."""
        threshold = threshold if threshold is not None else self._config.consolidation_threshold
        max_clusters = max_clusters if max_clusters is not None else self._config.consolidation_max_clusters
        if not hasattr(self._provider, '_semantic') or not self._provider._semantic:
            return []

        # 检测 embedder 类型，hash 需要更高阈值
        from agent.memory.embedding import SimpleHashEmbedder
        if isinstance(self._provider._semantic._embedder, SimpleHashEmbedder):
            threshold = max(threshold, self._config.consolidation_hash_threshold)

        # 获取所有记忆
        if not hasattr(self._provider, '_db') or not self._provider._db:
            return []

        cursor = await self._provider._db.execute(
            "SELECT memory_id, content FROM memories WHERE user_id = ? OR ? = '' ORDER BY updated_at DESC LIMIT 200",
            (user_id, user_id),
        )
        all_memories = []
        async for row in cursor:
            all_memories.append((row[0], row[1]))

        if len(all_memories) < 2:
            return []

        # Union-Find 聚类
        parent = {mid: mid for mid, _ in all_memories}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # 对每条记忆搜索 top-5 邻居
        for mid, content in all_memories:
            try:
                results = await self._provider._semantic.search(content, top_k=5, min_score=threshold)
                for r in results:
                    if r["id"] != mid and r["id"] in parent:
                        union(mid, r["id"])
            except (ValueError, TypeError) as e:
                logger.debug("Semantic search failed for memory %s: %s", mid[:8], e)
                continue

        # 收集聚类 (只要 size >= 2)
        clusters_map: dict[str, list[str]] = {}
        for mid, _ in all_memories:
            root = find(mid)
            clusters_map.setdefault(root, []).append(mid)

        clusters = []
        for mids in clusters_map.values():
            if len(mids) >= 2:
                memories = []
                for mid in mids:
                    mem = await self._provider.retrieve(mid)
                    if mem:
                        memories.append(mem)
                if len(memories) >= 2:
                    clusters.append(memories)
                if len(clusters) >= max_clusters:
                    break

        logger.info("Found %d memory clusters for consolidation", len(clusters))
        return clusters

    async def consolidate_cluster(
        self,
        cluster: list[Memory],
        model_router,
    ) -> Optional[dict]:
        """LLM 合并 N 条记忆为 1 条."""
        if not model_router or len(cluster) < 2:
            return None

        # 按时间排序 (旧→新)
        cluster.sort(key=lambda m: m.updated_at or m.created_at or 0)

        memories_text = "\n".join(
            f"{i+1}. [{m.memory_type.value if hasattr(m.memory_type, 'value') else m.memory_type}] {m.content}"
            for i, m in enumerate(cluster)
        )

        from agent.memory.llm_utils import llm_json_call
        prompt = CONSOLIDATION_PROMPT.format(n=len(cluster), memories_text=memories_text)
        result = await llm_json_call(model_router, prompt, max_retries=2)
        if isinstance(result, dict) and result.get("content"):
            return result
        return None

    async def run_consolidation(
        self,
        user_id: str = "",
        model_router=None,
    ) -> int:
        """完整合并管线: 找聚类 → 合并 → 删旧存新."""
        router = model_router or self._model_router
        if not router:
            return 0

        clusters = await self.find_similar_clusters(user_id=user_id)
        if not clusters:
            return 0

        consolidated_count = 0
        for cluster in clusters:
            merged = await self.consolidate_cluster(cluster, router)
            if not merged or not merged.get("content"):
                continue

            source_ids = [m.memory_id for m in cluster]
            new_memory = Memory(
                content=merged["content"],
                memory_type=MemoryType(merged.get("memory_type", "fact")),
                importance=MemoryImportance(merged.get("importance", "medium")),
                user_id=user_id,
                tags=merged.get("tags", []),
                metadata={"source": "consolidation", "consolidated_from": source_ids},
            )

            # 事务: store + delete + 记录历史 — 原子操作
            db = getattr(self._provider, '_db', None)
            if db:
                try:
                    await db.execute("BEGIN")
                    new_id = await self._provider.store(new_memory, _skip_commit=True)
                    for mid in source_ids:
                        await self._provider.delete(mid)
                    await db.execute(
                        "INSERT INTO memory_consolidations (consolidation_id, source_ids, result_id, strategy, created_at) VALUES (?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), json.dumps(source_ids), new_id, "merge", time.time()),
                    )
                    await db.execute("COMMIT")
                    consolidated_count += len(source_ids)
                    logger.info(
                        "Consolidated %d memories into %s: %s",
                        len(source_ids), new_id[:8], merged["content"][:50],
                    )
                except Exception as e:
                    await db.execute("ROLLBACK")
                    logger.warning("Consolidation transaction rolled back: %s", e)
                    continue
            else:
                # 无 DB 回退: 非事务模式
                new_id = await self._provider.store(new_memory)
                for mid in source_ids:
                    await self._provider.delete(mid)
                consolidated_count += len(source_ids)

        return consolidated_count
