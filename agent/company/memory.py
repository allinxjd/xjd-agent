"""CompanyMemory — 角色间共享记忆，复用 MemoryManager."""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

COMPANY_USER_ID = "company_shared"


class CompanyMemory:
    """角色间共享记忆层，封装 MemoryManager."""

    def __init__(self, memory_manager: Any = None) -> None:
        self._manager = memory_manager

    @property
    def available(self) -> bool:
        return self._manager is not None

    async def remember(
        self,
        content: str,
        role_name: str = "",
        tags: Optional[list[str]] = None,
        importance: str = "MEDIUM",
    ) -> Optional[str]:
        """存储共享记忆."""
        if not self._manager:
            return None

        try:
            from agent.memory.provider import MemoryImportance, MemoryType
            imp = getattr(MemoryImportance, importance, MemoryImportance.MEDIUM)
            memory_id = await self._manager.remember(
                content=content,
                user_id=COMPANY_USER_ID,
                memory_type=MemoryType.CONTEXT,
                importance=imp,
                tags=(tags or []) + ([f"role:{role_name}"] if role_name else []),
            )
            logger.debug("共享记忆存储: %s (by %s)", memory_id, role_name)
            return memory_id
        except Exception as e:
            logger.warning("共享记忆存储失败: %s", e)
            return None

    async def recall(
        self,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """检索共享记忆."""
        if not self._manager:
            return []

        try:
            results = await self._manager.recall(
                query=query,
                user_id=COMPANY_USER_ID,
                limit=limit,
            )
            return [
                {
                    "content": r.content,
                    "score": getattr(r, "score", 0),
                    "tags": getattr(r, "tags", []),
                    "memory_id": getattr(r, "memory_id", ""),
                }
                for r in results
            ]
        except Exception as e:
            logger.warning("共享记忆检索失败: %s", e)
            return []

    async def get_context(self, query: str) -> str:
        """获取与查询相关的共享记忆上下文，用于注入 prompt."""
        if not self._manager:
            return ""

        try:
            context, _ = await self._manager.get_memory_context(
                user_message=query,
                user_id=COMPANY_USER_ID,
            )
            return context
        except Exception as e:
            logger.warning("共享记忆上下文获取失败: %s", e)
            return ""

    async def extract_from_messages(
        self,
        messages: list[dict[str, str]],
        router: Any = None,
    ) -> list[str]:
        """从角色间消息中自动提取记忆."""
        if not self._manager:
            return []

        try:
            formatted = [
                {"role": "assistant", "content": m.get("content", "")}
                for m in messages
            ]
            return await self._manager.extract_from_conversation(
                messages=formatted,
                user_id=COMPANY_USER_ID,
                model_router=router,
            )
        except Exception as e:
            logger.warning("消息记忆提取失败: %s", e)
            return []
