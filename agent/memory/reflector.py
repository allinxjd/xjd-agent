"""自我反思器 — Agent 定期分析自身行为模式，生成元认知记忆。

流程:
1. 收集证据: 记忆统计、top 访问、低有用性、反馈信号
2. LLM 分析模式和知识缺口
3. 存为 META 类型记忆 + reflections 表
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from agent.memory.provider import Memory, MemoryImportance, MemoryType

logger = logging.getLogger(__name__)

REFLECTION_PROMPT = """分析以下关于你自身行为和记忆的数据，识别模式和改进方向。

记忆统计:
{stats}

最常访问的记忆 (top 5):
{top_accessed}

最低有用性的记忆 (bottom 5):
{low_usefulness}

最近反馈信号:
{recent_feedback}

请识别:
1. 行为模式 (behavior_pattern): 用户经常问什么类型的问题？
2. 知识缺口 (knowledge_gap): 哪些领域的记忆有用性低？
3. 优势 (strength): 哪些记忆最有帮助？
4. 改进方向 (improvement): 如何优化记忆策略？

返回 JSON 数组 (仅返回 JSON):
[{{"type": "behavior_pattern|knowledge_gap|strength|improvement", "insight": "发现内容", "action": "建议行动", "priority": "low|medium|high"}}]"""


class MemoryReflector:
    """自我反思器."""

    def __init__(self, provider, model_router=None):
        self._provider = provider
        self._model_router = model_router

    async def _gather_evidence(self, user_id: str = "") -> dict[str, Any]:
        """收集反思所需的证据数据."""
        evidence: dict[str, Any] = {}

        # 记忆统计
        if hasattr(self._provider, 'get_stats'):
            evidence["stats"] = await self._provider.get_stats()
        else:
            evidence["stats"] = {}

        # 最常访问的记忆
        all_memories = await self._provider.list_memories(user_id=user_id, limit=200)
        by_access = sorted(all_memories, key=lambda m: m.access_count, reverse=True)
        evidence["top_accessed"] = [
            f"- [{m.memory_type.value}] {m.content} (访问{m.access_count}次)"
            for m in by_access[:5]
        ]

        # 最低有用性的记忆
        by_usefulness = sorted(
            [m for m in all_memories if hasattr(m, 'feedback_count') and m.feedback_count > 0],
            key=lambda m: m.usefulness_score,
        )
        evidence["low_usefulness"] = [
            f"- [{m.memory_type.value}] {m.content} (有用性={m.usefulness_score:.2f})"
            for m in by_usefulness[:5]
        ]

        # 最近反馈
        evidence["recent_feedback"] = []
        if hasattr(self._provider, '_db') and self._provider._db:
            try:
                cursor = await self._provider._db.execute(
                    "SELECT memory_id, signal, created_at FROM memory_feedback ORDER BY created_at DESC LIMIT 10"
                )
                rows = await cursor.fetchall()
                for row in rows:
                    mem = await self._provider.retrieve(row[0])
                    content_preview = mem.content[:40] if mem else "(已删除)"
                    evidence["recent_feedback"].append(
                        f"- {row[1]}: {content_preview}"
                    )
            except (OSError, ValueError) as e:
                logger.debug("Failed to gather recent feedback: %s", e)

        return evidence

    async def reflect(
        self,
        user_id: str = "",
        model_router=None,
    ) -> list[str]:
        """执行自我反思，生成元认知记忆.

        Returns:
            生成的 memory_id 列表
        """
        router = model_router or self._model_router
        if not router:
            return []

        evidence = await self._gather_evidence(user_id)

        from agent.memory.llm_utils import llm_json_call
        prompt = REFLECTION_PROMPT.format(
            stats=json.dumps(evidence.get("stats") or {}, ensure_ascii=False, indent=2),
            top_accessed="\n".join(evidence.get("top_accessed", ["(无数据)"])),
            low_usefulness="\n".join(evidence.get("low_usefulness", ["(无数据)"])),
            recent_feedback="\n".join(evidence.get("recent_feedback", ["(无数据)"])),
        )

        insights = await llm_json_call(router, prompt, max_retries=2)
        if not isinstance(insights, list):
            return []

        memory_ids = []
        now = time.time()
        db = getattr(self._provider, '_db', None)

        # 事务包裹所有 store + reflections 插入
        if db:
            await db.execute("BEGIN")

        try:
            for item in insights:
                if not isinstance(item, dict) or not item.get("insight"):
                    continue

                rtype = item.get("type", "improvement")
                insight = item["insight"]
                action = item.get("action", "")

                meta_content = f"[{rtype}] {insight}"
                if action:
                    meta_content += f" → {action}"

                memory = Memory(
                    content=meta_content,
                    memory_type=MemoryType.META,
                    importance=MemoryImportance.MEDIUM,
                    user_id=user_id,
                    tags=["reflection", rtype],
                    metadata={"source": "self_reflection", "action": action},
                )
                memory_id = await self._provider.store(memory)
                memory_ids.append(memory_id)

                if db:
                    await db.execute(
                        "INSERT INTO reflections (reflection_id, reflection_type, content, action_items, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(uuid.uuid4()), rtype, insight, json.dumps([action]) if action else "[]", "active", now, now),
                    )

            if db:
                await db.execute("COMMIT")
        except (OSError, ValueError) as e:
            if db:
                await db.execute("ROLLBACK")
            logger.warning("Reflection transaction rolled back: %s", e)
            return []

        if memory_ids:
            logger.info("Self-reflection generated %d insights", len(memory_ids))

        return memory_ids

    async def get_knowledge_gaps(self, user_id: str = "") -> list[str]:
        """获取已识别的知识缺口，用于引导记忆提取."""
        if not hasattr(self._provider, '_db') or not self._provider._db:
            return []
        try:
            cursor = await self._provider._db.execute(
                "SELECT content FROM reflections WHERE reflection_type = 'knowledge_gap' AND status = 'active' ORDER BY updated_at DESC LIMIT 5"
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
        except (OSError, ValueError) as e:
            logger.debug("Failed to query knowledge gaps: %s", e)
            return []
