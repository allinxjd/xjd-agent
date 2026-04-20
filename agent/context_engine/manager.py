"""上下文引擎 — 自动管理对话上下文，防止超出 token 限制.

1. Token 计数 (tiktoken / 估算)
2. 滑动窗口 — 保留最新 N 轮对话
3. 自动摘要 — 压缩旧对话为摘要
4. 重要消息标记 — 保护关键上下文不被压缩

策略:
  messages 总 token 数 > max_context_tokens 时:
  1. 先尝试滑动窗口 (移除最旧的消息)
  2. 如果仍超出，对旧消息生成摘要替代
  3. system prompt 和最近 N 轮始终保留
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 每个字符的平均 token 数 (中文 ≈ 0.6, 英文 ≈ 0.25, 混合 ≈ 0.4)
AVG_TOKENS_PER_CHAR = 0.4

# 摘要生成 prompt
SUMMARIZE_PROMPT = """请将以下对话历史压缩为简洁的摘要，保留关键信息：
- 用户的关键需求和决策
- 工具调用的结果摘要
- 重要的上下文信息
- 不需要保留具体的代码细节或工具输出

对话:
{conversation}

请用 3-5 句话概括上述对话的核心内容:"""

class ContextEngine:
    """上下文引擎.

    用法:
        ctx = ContextEngine(max_tokens=100000)
        managed = await ctx.manage(messages, model_router)
    """

    def __init__(
        self,
        max_context_tokens: int = 100_000,
        reserve_for_response: int = 4096,
        min_recent_turns: int = 4,
        summary_threshold: float = 0.8,  # 80% 满时触发压缩
    ) -> None:
        self._max_tokens = max_context_tokens
        self._reserve = reserve_for_response
        self._min_recent = min_recent_turns
        self._threshold = summary_threshold
        self._summaries: list[str] = []

    @property
    def effective_max(self) -> int:
        return self._max_tokens - self._reserve

    def estimate_tokens(self, text: str) -> int:
        """估算 token 数."""
        if not text:
            return 0
        return max(1, int(len(text) * AVG_TOKENS_PER_CHAR))

    def count_message_tokens(self, messages: list[Any]) -> int:
        """统计消息列表的总 token 数."""
        total = 0
        for msg in messages:
            content = ""
            if hasattr(msg, "content"):
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
            elif isinstance(msg, dict):
                content = str(msg.get("content", ""))
            total += self.estimate_tokens(content) + 4  # 每条消息的 overhead
        return total

    async def manage(
        self,
        messages: list[Any],
        model_router: Optional[Any] = None,
    ) -> list[Any]:
        """管理消息上下文 — 确保不超出 token 限制.

        Args:
            messages: 完整消息列表 (包含 system prompt)
            model_router: 模型路由器 (用于生成摘要)

        Returns:
            裁剪/压缩后的消息列表
        """
        total_tokens = self.count_message_tokens(messages)

        # 没有超出限制
        if total_tokens <= self.effective_max:
            return messages

        logger.info(
            "Context overflow: %d tokens > %d max, compressing...",
            total_tokens, self.effective_max,
        )

        # 分离 system prompt 和历史消息
        system_msg = None
        history = list(messages)

        if history and self._get_role(history[0]) == "system":
            system_msg = history.pop(0)

        # 计算需要保留的最近消息 (至少 min_recent 轮 = min_recent * 2 条)
        recent_count = min(len(history), self._min_recent * 2)
        recent = history[-recent_count:]
        older = history[:-recent_count] if recent_count < len(history) else []

        # 策略 1: 滑动窗口 — 移除最旧的消息
        while older and self._total_tokens(system_msg, older, recent) > self.effective_max:
            older.pop(0)

        # 如果滑动窗口够了
        if self._total_tokens(system_msg, older, recent) <= self.effective_max:
            result = []
            if system_msg:
                result.append(system_msg)
            result.extend(older)
            result.extend(recent)
            logger.info(
                "Sliding window: removed %d messages, %d remaining",
                len(messages) - len(result), len(result),
            )
            return result

        # 策略 2: 摘要压缩 — 将 older 消息生成摘要
        if model_router and older:
            summary = await self._summarize(older, model_router)
            if summary:
                self._summaries.append(summary)
                # 用摘要消息替代所有旧消息
                from agent.providers.base import Message
                summary_msg = Message(
                    role="system",
                    content=f"[对话历史摘要]\n{summary}",
                )
                result = []
                if system_msg:
                    result.append(system_msg)
                result.append(summary_msg)
                result.extend(recent)
                logger.info("Summarized %d old messages into summary", len(older))
                return result

        # 策略 3: 暴力截断 (最后手段)
        result = []
        if system_msg:
            result.append(system_msg)
        result.extend(recent)
        logger.warning("Force-truncated context to %d messages", len(result))
        return result

    async def _summarize(
        self,
        messages: list[Any],
        model_router: Any,
    ) -> str:
        """用 LLM 生成对话摘要."""
        try:
            conversation = "\n".join(
                f"{self._get_role(m)}: {self._get_content(m)[:300]}"
                for m in messages
                if self._get_role(m) in ("user", "assistant")
            )

            if not conversation.strip():
                return ""

            from agent.providers.base import Message

            prompt = SUMMARIZE_PROMPT.format(conversation=conversation)
            response = await model_router.complete_with_failover(
                messages=[Message(role="user", content=prompt)],
                user_message=prompt,
                temperature=0.3,
            )
            return response.content.strip()

        except (OSError, RuntimeError) as e:
            logger.warning("Context summarization failed: %s", e)
            return ""

    def _total_tokens(self, system: Any, older: list, recent: list) -> int:
        msgs = []
        if system:
            msgs.append(system)
        msgs.extend(older)
        msgs.extend(recent)
        return self.count_message_tokens(msgs)

    def _get_role(self, msg: Any) -> str:
        if hasattr(msg, "role"):
            return msg.role
        if isinstance(msg, dict):
            return msg.get("role", "")
        return ""

    def _get_content(self, msg: Any) -> str:
        if hasattr(msg, "content"):
            return msg.content if isinstance(msg.content, str) else str(msg.content)
        if isinstance(msg, dict):
            return str(msg.get("content", ""))
        return ""

    # ── Compact (on-demand + auto) ──

    async def compact(
        self,
        messages: list[Any],
        model_router: Optional[Any] = None,
    ) -> tuple[list[Any], dict[str, Any]]:
        """手动压缩对话历史 (/compact 命令).

        Returns:
            (压缩后的消息列表, 统计信息)
        """
        before_tokens = self.count_message_tokens(messages)
        before_count = len(messages)

        # 分离 system prompt
        system_msg = None
        history = list(messages)
        if history and self._get_role(history[0]) == "system":
            system_msg = history.pop(0)

        # 保留最近 2 轮
        recent_count = min(len(history), self._min_recent * 2)
        recent = history[-recent_count:]
        older = history[:-recent_count] if recent_count < len(history) else []

        # 生成摘要
        summary_text = ""
        if model_router and older:
            summary_text = await self._summarize(older, model_router)

        if not summary_text and older:
            # 无法生成摘要时，简单截断
            summary_text = f"[已压缩 {len(older)} 条历史消息]"

        result = []
        if system_msg:
            result.append(system_msg)
        if summary_text:
            from agent.providers.base import Message
            result.append(Message(role="system", content=f"[对话历史摘要]\n{summary_text}"))
        result.extend(recent)

        after_tokens = self.count_message_tokens(result)

        stats = {
            "before_messages": before_count,
            "after_messages": len(result),
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": before_tokens - after_tokens,
            "compression_ratio": round(after_tokens / max(before_tokens, 1), 2),
            "summarized_messages": len(older),
        }

        logger.info("Compact: %d→%d messages, %d→%d tokens", before_count, len(result), before_tokens, after_tokens)
        return result, stats

    def should_auto_compact(self, messages: list[Any]) -> bool:
        """检查是否应自动触发压缩."""
        total = self.count_message_tokens(messages)
        threshold = int(self.effective_max * self._threshold)
        return total > threshold

    def get_usage_stats(self, messages: list[Any]) -> dict[str, Any]:
        """获取当前上下文使用统计."""
        total = self.count_message_tokens(messages)
        return {
            "current_tokens": total,
            "max_tokens": self.effective_max,
            "usage_percent": round(total / max(self.effective_max, 1) * 100, 1),
            "message_count": len(messages),
            "summaries_generated": len(self._summaries),
        }
