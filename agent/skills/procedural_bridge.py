"""程序记忆桥接 — 将 SkillManager 与 MemorySystem 双向同步.

第 4 层记忆: 程序记忆 (Procedural Memory)
= 技能本身作为记忆存储，使得记忆系统可以检索到"怎么做某件事"。

同步规则:
- 技能创建 → 自动写入 PROCEDURAL 记忆
- 技能更新 → 更新对应记忆
- 技能删除 → 删除对应记忆
- 记忆搜索 → 可以搜到技能步骤
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

class ProceduralBridge:
    """程序记忆桥接器.

    用法:
        bridge = ProceduralBridge(memory_manager)
        skill_manager.set_procedural_bridge(bridge)

        # 之后 skill_manager 的 create/update/delete 会自动同步
    """

    def __init__(self, memory_manager: Any) -> None:
        self._memory_manager = memory_manager

    def _skill_to_memory_content(self, skill: Any) -> str:
        """将技能转为记忆内容 (人类可读)."""
        steps_desc = []
        for i, step in enumerate(skill.steps, 1):
            desc = step.get("description", "")
            tool = step.get("tool", "")
            if tool:
                steps_desc.append(f"{i}. {desc} (工具: {tool})")
            else:
                steps_desc.append(f"{i}. {desc}")

        return (
            f"技能: {skill.name}\n"
            f"描述: {skill.description}\n"
            f"触发: {skill.trigger}\n"
            f"步骤:\n" + "\n".join(steps_desc)
        )

    async def on_skill_created(self, skill: Any) -> Optional[str]:
        """技能创建时同步到记忆."""
        try:
            from agent.memory.provider import MemoryType, MemoryImportance

            content = self._skill_to_memory_content(skill)
            memory_id = await self._memory_manager.remember(
                content=content,
                memory_type=MemoryType.PROCEDURAL,
                importance=MemoryImportance.HIGH,
                tags=["skill", skill.category] + skill.tags,
                metadata={
                    "skill_id": skill.skill_id,
                    "skill_name": skill.name,
                    "version": skill.version,
                },
            )
            logger.debug("Synced skill %s to procedural memory %s", skill.skill_id, memory_id)
            return memory_id
        except Exception as e:
            logger.warning("Failed to sync skill to memory: %s", e)
            return None

    async def on_skill_updated(self, skill: Any) -> bool:
        """技能更新时同步记忆."""
        try:
            from agent.memory.provider import MemoryType

            # 找到对应的记忆
            results = await self._memory_manager.recall(
                query=f"技能: {skill.name}",
                memory_types=[MemoryType.PROCEDURAL],
                limit=1,
            )

            if results:
                memory = results[0].memory
                content = self._skill_to_memory_content(skill)
                await self._memory_manager._provider.update(
                    memory.memory_id,
                    {
                        "content": content,
                        "metadata": {
                            "skill_id": skill.skill_id,
                            "skill_name": skill.name,
                            "version": skill.version,
                        },
                    },
                )
                return True
            else:
                # 记忆不存在，创建新的
                await self.on_skill_created(skill)
                return True
        except Exception as e:
            logger.warning("Failed to update procedural memory: %s", e)
            return False

    async def on_skill_deleted(self, skill_id: str, skill_name: str = "") -> bool:
        """技能删除时清理记忆."""
        try:
            from agent.memory.provider import MemoryType

            results = await self._memory_manager.recall(
                query=f"技能: {skill_name}" if skill_name else skill_id,
                memory_types=[MemoryType.PROCEDURAL],
                limit=5,
            )

            deleted = False
            for r in results:
                meta = r.memory.metadata or {}
                if meta.get("skill_id") == skill_id:
                    await self._memory_manager._provider.delete(r.memory.memory_id)
                    deleted = True
                    break

            return deleted
        except Exception as e:
            logger.warning("Failed to delete procedural memory: %s", e)
            return False

    async def sync_all_skills(self, skill_manager: Any) -> int:
        """全量同步所有技能到记忆系统."""
        skills = await skill_manager.list_skills()
        count = 0
        for skill in skills:
            if "deprecated" not in skill.tags:
                mid = await self.on_skill_created(skill)
                if mid:
                    count += 1
        logger.info("Synced %d skills to procedural memory", count)
        return count
