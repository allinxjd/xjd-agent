"""电商协调器 — 意图分类 + 多 Agent 委派 + 结果聚合.

继承 MultiAgentManager，增加电商特有的意图路由和跨域协作能力。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from agent.core.multi_agent import MultiAgentManager, AgentRole, SubAgentResult
from agent.ecommerce.roles import ECOMMERCE_ROLES
from agent.ecommerce.shared_memory import SharedMemory

logger = logging.getLogger(__name__)

# 意图 → 角色映射 (可匹配多个角色)
INTENT_ROLE_MAP: dict[str, list[str]] = {
    "order_query": ["order"],
    "shipping_track": ["order"],
    "return_refund": ["order", "customer_service"],
    "inventory_check": ["inventory"],
    "product_search": ["inventory", "marketing"],
    "complaint": ["customer_service"],
    "recommendation": ["marketing"],
    "promotion": ["marketing"],
    "image_generation": [],  # 空列表 → 交给主引擎处理
    "general": ["customer_service"],
}


class ECommerceCoordinator(MultiAgentManager):
    """电商协调器.

    用法:
        coordinator = ECommerceCoordinator(router, tool_registry, redis_manager)
        reply = await coordinator.handle_message("查一下订单 12345 的物流")
    """

    def __init__(
        self,
        router: Any,
        tool_registry: Any,
        redis: Any = None,
        **kwargs,
    ) -> None:
        super().__init__(router, tool_registry, **kwargs)

        # 注册电商角色
        for role in ECOMMERCE_ROLES:
            self.register_role(role)

        # 共享记忆
        self._shared_memory: Optional[SharedMemory] = None
        if redis:
            self._shared_memory = SharedMemory(redis)

    async def handle_message(
        self,
        message: str,
        session_id: str = "",
        parent_messages: Optional[list] = None,
    ) -> str:
        """处理用户消息 — 意图分类 → 委派 → 聚合.

        Args:
            message: 用户消息文本
            session_id: 会话 ID (用于共享记忆)
            parent_messages: 上下文消息

        Returns:
            聚合后的回复文本
        """
        # 1. 意图分类
        intents = self._classify_intent(message)
        logger.info("意图分类: %s → %s", message[:50], intents)

        # 2. 确定需要的角色
        roles = set()
        for intent in intents:
            for role in INTENT_ROLE_MAP.get(intent, ["customer_service"]):
                roles.add(role)

        # 3. 检查共享记忆 (避免重复查询)
        cached_context = ""
        if self._shared_memory and session_id:
            mem = await self._shared_memory.read_all(session_id)
            if mem:
                cached_context = "\n".join(
                    f"[已知] {k}: {v}" for k, v in mem.items()
                )

        # 4. 委派
        task_prompt = message
        if cached_context:
            task_prompt = f"{message}\n\n已有上下文:\n{cached_context}"

        if len(roles) == 1:
            # 单角色: 直接委派
            role_name = roles.pop()
            result = await self.spawn_agent(role_name, task_prompt, parent_messages)
            reply = result.content if result.success else f"处理失败: {result.error}"
        else:
            # 多角色: 并行委派
            tasks = [{"task": task_prompt, "role": r} for r in roles]
            results = await self.parallel_delegate(tasks)
            reply = self._aggregate_results(results)

        # 5. 写入共享记忆
        if self._shared_memory and session_id:
            await self._shared_memory.write(
                session_id, f"last_reply_{int(time.time())}",
                {"intents": intents, "roles": list(roles), "reply_preview": reply[:200]},
                agent_id="coordinator",
            )

        return reply

    def _classify_intent(self, message: str) -> list[str]:
        """基于关键词的意图分类 (轻量级，不依赖 LLM)."""
        lower = message.lower()
        intents = []

        intent_keywords = {
            "order_query": ["订单", "order", "查单"],
            "shipping_track": ["物流", "快递", "发货", "配送", "shipping", "tracking"],
            "return_refund": ["退货", "退款", "换货", "退换", "refund", "return"],
            "inventory_check": ["库存", "有货", "缺货", "stock", "inventory"],
            "product_search": ["搜索", "找", "有没有", "search", "商品"],
            "complaint": ["投诉", "差评", "质量", "complaint"],
            "recommendation": ["推荐", "类似", "相似", "recommend"],
            "promotion": ["优惠", "促销", "折扣", "优惠券", "discount", "coupon"],
            "image_generation": [
                "做图", "做一张", "生成图", "海报", "主图", "白底图", "详情图",
                "种草图", "电商图", "产品图", "图片生成", "设计图", "banner",
                "poster", "image", "generate", "做张", "帮我做",
                "竞品", "调研", "研究", "先看看", "参考", "对比",
            ],
        }

        for intent, keywords in intent_keywords.items():
            if any(kw in lower for kw in keywords):
                intents.append(intent)

        return intents or ["general"]

    def _aggregate_results(self, results: list[SubAgentResult]) -> str:
        """聚合多个 Agent 的结果."""
        parts = []
        for r in results:
            if r.success and r.content:
                parts.append(r.content)
            elif not r.success:
                logger.warning("Agent %s 失败: %s", r.agent_name, r.error)

        if not parts:
            return "抱歉，暂时无法处理您的请求。请稍后重试。"

        if len(parts) == 1:
            return parts[0]

        # 多结果合并
        return "\n\n---\n\n".join(parts)
