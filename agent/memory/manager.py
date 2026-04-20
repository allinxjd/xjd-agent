"""记忆管理器 — 核心职责:
1. 从对话中自动提取值得记住的信息
2. 在每轮对话前注入相关记忆到上下文
3. 管理记忆的生命周期 (创建/更新/淘汰)
4. 支持主动学习 (用户可以要求记住/忘记)
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from agent.memory.provider import (
    BaseMemoryProvider,
    BuiltinMemoryProvider,
    Memory,
    MemoryImportance,
    MemorySearchResult,
    MemoryType,
)
from agent.memory.metrics import get_metrics as _get_metrics

logger = logging.getLogger(__name__)

# user_id 格式校验 — 只允许字母数字下划线连字符，最长 128 字符
_VALID_USER_ID = re.compile(r"^[a-zA-Z0-9_\-]{0,128}$")

def _sanitize_user_id(user_id: str) -> str:
    """校验并返回安全的 user_id."""
    if not _VALID_USER_ID.match(user_id):
        raise ValueError(f"Invalid user_id format: {user_id!r}")
    return user_id

# 记忆提取 prompt — 让模型从对话中提取记忆
MEMORY_EXTRACTION_PROMPT = """分析以下对话，提取值得长期记住的信息。

提取规则:
1. 用户明确说"记住"、"记一下"、"不要忘了"的内容 → importance=critical
2. 用户的个人信息 (名字、职业、位置等) → importance=high, type=fact
3. 用户的偏好 (喜好、习惯、风格) → importance=high, type=preference
4. 项目/工作相关的上下文信息 → importance=medium, type=context
5. 普通对话不需要提取

返回 JSON 数组 (如无可提取内容，返回空数组 []):
[
  {{
    "content": "记忆内容 (简洁明了)",
    "memory_type": "fact|preference|context|episodic",
    "importance": "low|medium|high|critical",
    "tags": ["tag1", "tag2"]
  }}
]

对话内容:
{conversation}

请分析并返回 JSON (仅返回 JSON，不要其他内容):"""

# 记忆注入 prompt — 将检索到的记忆注入到系统提示
MEMORY_INJECTION_TEMPLATE = """
## 已知信息 (来自记忆)
以下是关于用户的已知信息，你可以在对话中自然地使用这些信息:

{memories}

注意: 不要直接说"根据我的记忆"，而是自然地将这些信息融入对话。
"""

class MemoryManager:
    """记忆管理器.

    用法:
        manager = MemoryManager()
        await manager.initialize()

        # 存储记忆
        await manager.remember("用户名字叫张三", user_id="user123")

        # 检索相关记忆
        memories = await manager.recall("你好，我是谁", user_id="user123")

        # 从对话中自动提取记忆
        await manager.extract_from_conversation(messages, user_id="user123")

        # 生成记忆注入文本
        context = await manager.get_memory_context(user_message, user_id="user123")
    """

    def __init__(
        self,
        provider: Optional[BaseMemoryProvider] = None,
        auto_extract: bool = True,
        config: Optional["MemoryConfig"] = None,
        # 兼容旧调用方式
        max_injection: int = 8,
        extract_interval: int = 3,
    ) -> None:
        from agent.memory.config import MemoryConfig
        self._config = config or MemoryConfig(
            max_injection=max_injection,
            extract_interval=extract_interval,
        )
        self._provider = provider or BuiltinMemoryProvider()
        self._auto_extract = auto_extract
        self._max_injection = self._config.max_injection
        self._extract_interval = self._config.extract_interval
        self._turn_counter: dict[str, int] = {}
        self._initialized = False

    async def initialize(self) -> None:
        """初始化."""
        if not self._initialized:
            await self._provider.initialize()
            self._initialized = True
            logger.info("MemoryManager initialized")

    async def remember(
        self,
        content: str,
        user_id: str = "",
        memory_type: MemoryType = MemoryType.FACT,
        importance: MemoryImportance = MemoryImportance.MEDIUM,
        tags: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """主动记住信息.

        Returns:
            memory_id
        """
        user_id = _sanitize_user_id(user_id)
        await self.initialize()

        # 检查是否已有类似记忆 (避免重复)
        # 1. LIKE 子串搜索
        search_len = min(len(content), 20)
        search_query = content[:search_len]
        existing = await self._provider.search(
            query=search_query,
            user_id=user_id,
            limit=5,
        )

        best_match = None
        best_score = 0.0

        for result in existing:
            score = result.relevance_score
            existing_content = result.memory.content
            # 内容包含关系 → 高度相似
            if existing_content in content or content in existing_content:
                score = max(score, 0.85)
            # 2-gram 文本重叠
            text_overlap = self._compute_text_overlap(content, existing_content)
            combined = max(score, text_overlap)
            if combined > best_score:
                best_score = combined
                best_match = result

        # 2. 语义搜索补充 (如果可用)
        if best_score < self._config.dedup_threshold and hasattr(self._provider, '_semantic') and self._provider._semantic:
            try:
                from agent.memory.embedding import SimpleHashEmbedder
                is_hash = isinstance(self._provider._semantic._embedder, SimpleHashEmbedder)
                min_score = 0.01 if is_hash else 0.3
                sem_results = await self._provider._semantic.search(content, top_k=3, min_score=min_score)
                for sr in sem_results:
                    mem = await self._provider.retrieve(sr["id"])
                    if not mem or (user_id and mem.user_id != user_id):
                        continue
                    text_overlap = self._compute_text_overlap(content, mem.content)
                    combined = self._config.dedup_text_weight * text_overlap + self._config.dedup_semantic_weight * sr["score"]
                    if combined > best_score:
                        best_score = combined
                        best_match = MemorySearchResult(
                            memory=mem, relevance_score=combined, match_type="semantic",
                        )
            except (OSError, ValueError) as e:
                logger.debug("Semantic dedup search failed: %s", e)

        # 3. 如果找到高度相似的，更新而非新建
        if best_match and best_score > self._config.dedup_threshold:
            await self._provider.update(best_match.memory.memory_id, {
                "content": content,
                "importance": importance.value,
            })
            logger.info("Updated existing memory: %s (score=%.2f)", best_match.memory.memory_id[:8], best_score)
            _get_metrics().store_count += 1
            _get_metrics().store_dedup_count += 1
            return best_match.memory.memory_id

        # 创建新记忆
        memory = Memory(
            content=content,
            memory_type=memory_type,
            importance=importance,
            user_id=user_id,
            tags=tags or [],
            metadata=metadata or {},
        )

        memory_id = await self._provider.store(memory)
        _get_metrics().store_count += 1
        logger.info(
            "Stored new memory: %s [%s] %s",
            memory_id[:8], memory_type.value, content[:50],
        )
        return memory_id

    @staticmethod
    def _compute_text_overlap(a: str, b: str) -> float:
        """2-gram Jaccard 相似度."""
        if not a or not b:
            return 0.0
        grams_a = {a[i:i+2] for i in range(max(1, len(a) - 1))}
        grams_b = {b[i:i+2] for i in range(max(1, len(b) - 1))}
        if not grams_a or not grams_b:
            return 0.0
        intersection = len(grams_a & grams_b)
        union = len(grams_a | grams_b)
        return intersection / union if union > 0 else 0.0

    async def consolidate(
        self,
        user_id: str = "",
        model_router=None,
    ) -> int:
        """合并语义相似的记忆.

        Returns:
            合并的记忆数量
        """
        user_id = _sanitize_user_id(user_id)
        await self.initialize()
        consolidator = MemoryConsolidator(self._provider, model_router=model_router)
        return await consolidator.run_consolidation(user_id=user_id, model_router=model_router)

    async def record_feedback(
        self,
        memory_ids: list[str],
        signal: str,  # 'positive' | 'negative'
    ) -> None:
        """记录记忆有用性反馈.

        在每轮对话后调用:
        - 成功回复 → positive (注入的记忆有帮助)
        - 失败回复 → negative (注入的记忆没帮助)

        使用 EMA 更新 usefulness_score: new = 0.8 * old + 0.2 * signal_value
        """
        if not memory_ids:
            return
        await self.initialize()

        import uuid as _uuid
        signal_value = 1.0 if signal == "positive" else 0.0
        if signal == "positive":
            _get_metrics().feedback_positive += len(memory_ids)
        else:
            _get_metrics().feedback_negative += len(memory_ids)

        for mid in memory_ids:
            try:
                mem = await self._provider.retrieve(mid)
                if not mem:
                    continue
                old_score = mem.usefulness_score if hasattr(mem, 'usefulness_score') else 0.5
                alpha = self._config.feedback_ema_alpha
                new_score = alpha * old_score + (1 - alpha) * signal_value
                new_count = (mem.feedback_count if hasattr(mem, 'feedback_count') else 0) + 1

                await self._provider.update(mid, {
                    "usefulness_score": round(new_score, 4),
                    "feedback_count": new_count,
                })

                # 记录反馈事件
                if hasattr(self._provider, '_db') and self._provider._db:
                    await self._provider._db.execute(
                        "INSERT INTO memory_feedback (feedback_id, memory_id, signal, created_at) VALUES (?, ?, ?, ?)",
                        (str(_uuid.uuid4()), mid, signal, time.time()),
                    )
                    await self._provider._db.commit()
            except (OSError, ValueError) as e:
                logger.debug("Record feedback failed for %s: %s", mid[:8], e)

    async def forget(self, memory_id: str) -> bool:
        """忘记 (删除) 记忆."""
        await self.initialize()
        return await self._provider.delete(memory_id)

    async def recall(
        self,
        query: str,
        user_id: str = "",
        memory_types: Optional[list[MemoryType]] = None,
        limit: int = 10,
    ) -> list[MemorySearchResult]:
        """回忆 — 搜索相关记忆.

        Args:
            query: 搜索查询 (通常是用户最新消息)
            user_id: 用户 ID
            memory_types: 限定类型
            limit: 最大返回数

        Returns:
            相关记忆列表
        """
        user_id = _sanitize_user_id(user_id)
        await self.initialize()
        return await self._provider.search(
            query=query,
            user_id=user_id,
            memory_types=memory_types,
            limit=limit,
        )

    async def get_memory_context(
        self,
        user_message: str,
        user_id: str = "",
    ) -> tuple[str, list[str]]:
        """生成记忆上下文注入文本.

        在每轮对话前调用，将相关记忆注入到 system prompt。

        Returns:
            (记忆注入文本, 注入的 memory_id 列表)
        """
        if not user_message.strip():
            return "", []
        user_id = _sanitize_user_id(user_id)

        await self.initialize()

        # 搜索相关记忆
        results = await self.recall(
            query=user_message,
            user_id=user_id,
            limit=self._max_injection,
        )

        if not results:
            return "", []

        # 构建记忆文本
        memory_lines = []
        memory_ids = []
        for i, r in enumerate(results, 1):
            m = r.memory
            type_label = {
                MemoryType.FACT: "事实",
                MemoryType.PREFERENCE: "偏好",
                MemoryType.SKILL: "技能",
                MemoryType.EPISODIC: "经历",
                MemoryType.CONTEXT: "上下文",
                MemoryType.RELATIONSHIP: "关系",
                MemoryType.META: "自我认知",
            }.get(m.memory_type, "信息")
            memory_lines.append(f"- [{type_label}] {m.content}")
            memory_ids.append(m.memory_id)

        memories_text = "\n".join(memory_lines)

        # 注入元认知记忆 (最近 3 条 META 类型)
        try:
            meta_results = await self.recall(
                query=user_message,
                user_id=user_id,
                memory_types=[MemoryType.META],
                limit=3,
            )
            if meta_results:
                meta_lines = [f"- {r.memory.content}" for r in meta_results]
                memories_text += "\n\n## 自我认知\n" + "\n".join(meta_lines)
        except (ValueError, KeyError) as e:
            logger.debug("Meta memory injection failed: %s", e)

        return MEMORY_INJECTION_TEMPLATE.format(memories=memories_text), memory_ids

    async def extract_from_conversation(
        self,
        messages: list[dict[str, Any]],
        user_id: str = "",
        model_router: Optional[Any] = None,
    ) -> list[str]:
        """从对话中自动提取记忆.

        使用 LLM 分析对话内容，提取值得记住的信息。

        Args:
            messages: 对话历史
            user_id: 用户 ID
            model_router: ModelRouter 实例 (用于调用 LLM)

        Returns:
            提取的 memory_id 列表
        """
        if not model_router or not messages:
            return []
        user_id = _sanitize_user_id(user_id)

        await self.initialize()

        # 更新 turn counter
        self._turn_counter.setdefault(user_id, 0)
        self._turn_counter[user_id] += 1

        # 按间隔提取
        if self._turn_counter[user_id] % self._extract_interval != 0:
            return []

        # 构建对话文本
        recent_messages = messages[-10:]  # 最近 10 条
        conversation = "\n".join(
            f"{m.get('role', 'unknown')}: {m.get('content', '')}"
            for m in recent_messages
            if m.get("role") in ("user", "assistant")
        )

        if len(conversation) < 20:
            return []

        # 调用 LLM 提取
        from agent.memory.llm_utils import llm_json_call

        prompt = MEMORY_EXTRACTION_PROMPT.format(conversation=conversation)

        # 反思引导: 追加已知知识缺口
        try:
            from agent.memory.reflector import MemoryReflector
            reflector = MemoryReflector(self._provider)
            gaps = await reflector.get_knowledge_gaps(user_id=user_id)
            if gaps:
                gap_text = "\n".join(f"- {g}" for g in gaps)
                prompt += f"\n\n已知知识缺口 (优先提取):\n{gap_text}"
        except (ValueError, KeyError) as e:
            logger.debug("Knowledge gap retrieval failed: %s", e)

        extracted = await llm_json_call(model_router, prompt, max_retries=2)
        if not isinstance(extracted, list):
            return []

        memory_ids = []
        for item in extracted:
            if not isinstance(item, dict) or not item.get("content"):
                continue

            memory_id = await self.remember(
                content=item["content"],
                user_id=user_id,
                memory_type=MemoryType(item.get("memory_type", "fact")),
                importance=MemoryImportance(item.get("importance", "medium")),
                tags=item.get("tags", []),
                metadata={"source": "auto_extract"},
            )
            memory_ids.append(memory_id)

        if memory_ids:
            _get_metrics().extraction_runs += 1
            _get_metrics().extraction_memories += len(memory_ids)
            logger.info(
                "Auto-extracted %d memories from conversation (user=%s)",
                len(memory_ids), user_id,
            )

        return memory_ids

    async def on_turn_complete(
        self,
        user_message: str,
        assistant_response: str,
        user_id: str = "",
        model_router: Optional[Any] = None,
    ) -> None:
        """每轮对话完成后的回调.

        自动执行:
        1. 检查是否有"记住"指令
        2. 按间隔自动提取记忆
        """
        if not self._auto_extract:
            return
        user_id = _sanitize_user_id(user_id)

        # 检查用户是否明确要求记住
        remember_triggers = ["记住", "记一下", "不要忘", "记得", "remember", "note that"]
        lower_msg = user_message.lower()
        for trigger in remember_triggers:
            if trigger in lower_msg:
                # 直接存储
                content = user_message
                for t in remember_triggers:
                    content = content.replace(t, "").strip()
                if content:
                    await self.remember(
                        content=content,
                        user_id=user_id,
                        importance=MemoryImportance.HIGH,
                    )
                break

    async def list_memories(
        self,
        user_id: str = "",
        memory_type: Optional[MemoryType] = None,
    ) -> list[Memory]:
        """列出记忆."""
        await self.initialize()
        return await self._provider.list_memories(
            user_id=user_id,
            memory_type=memory_type,
        )

    async def get_stats(self) -> dict[str, Any]:
        """获取统计."""
        await self.initialize()
        return await self._provider.get_stats()

    async def decay_memories(
        self,
        user_id: str = "",
        max_memories: Optional[int] = None,
    ) -> int:
        """衰减淘汰低价值记忆."""
        user_id = _sanitize_user_id(user_id)
        await self.initialize()

        all_memories = await self._provider.list_memories(user_id=user_id, limit=(max_memories or 500) * 2)
        if not all_memories:
            return 0

        now = time.time()
        DAY = 86400
        deleted = 0
        downgraded = 0

        for m in all_memories:
            age_days = (now - m.updated_at) / DAY if m.updated_at else 0

            # 规则 1: low + 零访问 + 30天 → 删除
            if (
                m.importance == MemoryImportance.LOW
                and m.access_count == 0
                and age_days > self._config.decay_low_days
            ):
                await self._provider.delete(m.memory_id)
                deleted += 1
                continue

            # 规则 2: medium + 零访问 + 90天 → 降级为 low
            if (
                m.importance == MemoryImportance.MEDIUM
                and m.access_count == 0
                and age_days > self._config.decay_medium_days
            ):
                await self._provider.update(m.memory_id, {"importance": "low"})
                downgraded += 1

        # 规则 3: 总数超限 → 按 score 淘汰
        remaining = await self._provider.list_memories(user_id=user_id, limit=(max_memories or 500) * 2)
        if max_memories and len(remaining) > max_memories:
            importance_weights = {"low": 0.1, "medium": 0.4, "high": 0.7, "critical": 1.0}

            scored = []
            for m in remaining:
                age_days = (now - m.updated_at) / DAY if m.updated_at else 365
                imp_w = importance_weights.get(m.importance.value, 0.4)
                recency_w = max(0, 1.0 - age_days / 365)
                access_w = min(1.0, m.access_count / 10)
                useful_w = m.usefulness_score if hasattr(m, 'usefulness_score') else 0.5
                score = imp_w * self._config.decay_weight_importance + recency_w * self._config.decay_weight_recency + access_w * self._config.decay_weight_access + useful_w * self._config.decay_weight_usefulness
                scored.append((m, score))

            scored.sort(key=lambda x: x[1])
            to_remove = len(remaining) - max_memories
            for m, score in scored[:to_remove]:
                if m.importance == MemoryImportance.CRITICAL:
                    continue  # 永不淘汰 critical
                await self._provider.delete(m.memory_id)
                deleted += 1

        if deleted or downgraded:
            _get_metrics().decay_deleted += deleted
            _get_metrics().decay_downgraded += downgraded
            logger.info(
                "Memory decay: deleted=%d, downgraded=%d (user=%s)",
                deleted, downgraded, user_id or "global",
            )

        # 清理辅助表 — 防止无界增长
        db = getattr(self._provider, '_db', None)
        if db:
            try:
                await db.execute(
                    "DELETE FROM reflections WHERE reflection_id NOT IN "
                    "(SELECT reflection_id FROM reflections ORDER BY created_at DESC LIMIT ?)",
                    (int(self._config.max_reflections),)
                )
                cutoff = now - self._config.feedback_retention_days * DAY
                await db.execute(
                    "DELETE FROM memory_feedback WHERE created_at < ?", (cutoff,)
                )
                await db.execute(
                    "DELETE FROM memory_consolidations WHERE consolidation_id NOT IN "
                    "(SELECT consolidation_id FROM memory_consolidations ORDER BY created_at DESC LIMIT ?)",
                    (int(self._config.max_consolidation_history),)
                )
                await db.commit()
            except (OSError, ValueError) as e:
                logger.debug("Auxiliary table cleanup failed: %s", e)

        try:
            meta_memories = await self._provider.list_memories(
                user_id=user_id, memory_type=MemoryType.META,
            )
            if len(meta_memories) > self._config.max_meta_memories:
                meta_sorted = sorted(meta_memories, key=lambda m: m.created_at or 0)
                for m in meta_sorted[:len(meta_memories) - self._config.max_meta_memories]:
                    await self._provider.delete(m.memory_id)
                    deleted += 1
        except (OSError, ValueError) as e:
            logger.debug("META memory cleanup failed: %s", e)

        return deleted

    async def close(self) -> None:
        """关闭."""
        await self._provider.close()
