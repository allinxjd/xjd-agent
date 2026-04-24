"""学习闭环 — 核心学习循环:
1. 完成任务 → 判断是否成功
2. 提取技能 → 将成功流程固化为可复用技能
3. 优化技能 → 根据新经验更新已有技能
4. 持久记忆 → 将关键信息存入长期记忆

与 AgentEngine 的集成:
    engine.run_turn() 完成后，调用 learning_loop.on_turn_complete()
    学习闭环自动执行提取和记忆。

                  ┌─────────────┐
                  │  用户请求    │
                  └──────┬──────┘
                         ▼
                  ┌─────────────┐
                  │ Agent 执行   │
                  │  (tool loop) │
                  └──────┬──────┘
                         ▼
              ┌──────────────────────┐
              │   学习闭环触发        │
              │  on_turn_complete()  │
              └──────────┬───────────┘
                         ▼
            ┌────────────────────────┐
            │ 1. 评估: 任务是否成功？  │
            └────────────┬───────────┘
                    ┌────┴────┐
                    │         │
                  成功       失败
                    │         │
                    ▼         ▼
            ┌──────────┐ ┌──────────┐
            │2.提取技能 │ │ 记录失败  │
            └──────┬───┘ │ 模式     │
                   │     └──────────┘
                   ▼
            ┌──────────┐
            │3.优化技能 │ (如已有类似技能)
            └──────┬───┘
                   ▼
            ┌──────────┐
            │4.持久记忆 │ (提取对话中的关键信息)
            └──────────┘
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

@dataclass
class InjectedContext:
    """注入上下文 — 分离 system prompt 和 user message.

    system_context: 记忆 + 技能概览 (稳定，prompt cache 友好)
    skill_message: 匹配到的技能完整内容 (作为 user message 注入)
    matched_skill_id: 匹配到的技能 ID (用于记录使用)
    """

    system_context: str = ""
    skill_message: str = ""
    matched_skill_id: str = ""
    injected_memory_ids: list[str] = field(default_factory=list)


@dataclass
class LearningStats:
    """学习统计."""

    total_turns: int = 0
    successful_turns: int = 0
    failed_turns: int = 0
    skills_created: int = 0
    skills_updated: int = 0
    skills_refined: int = 0
    memories_extracted: int = 0
    last_learning_time: float = 0.0

class LearningLoop:
    """学习闭环 — 从经验中学习.

    用法:
        loop = LearningLoop(memory_manager, skill_manager)

        # 在每轮对话完成后调用
        await loop.on_turn_complete(
            messages=engine.messages,
            result=turn_result,
            user_id="user123",
            model_router=router,
        )
    """

    def __init__(
        self,
        memory_manager: Optional[Any] = None,  # MemoryManager
        skill_manager: Optional[Any] = None,   # SkillManager
        skill_optimizer: Optional[Any] = None,  # SkillOptimizer
        learning_enabled: bool = True,
        config: Optional[Any] = None,  # MemoryConfig
        pin_manager: Optional[Any] = None,  # ContextPinManager
        tool_registry: Optional[Any] = None,  # ToolRegistry
        # 兼容旧调用
        min_tool_calls_for_skill: int = 2,
        learning_interval: int = 5,
    ) -> None:
        from agent.memory.config import MemoryConfig
        self._config = config or MemoryConfig(
            min_tool_calls_for_skill=min_tool_calls_for_skill,
            learning_interval=learning_interval,
        )
        self._memory_manager = memory_manager
        self._skill_manager = skill_manager
        self._skill_optimizer = skill_optimizer
        self._pin_manager = pin_manager
        self._tool_registry = tool_registry
        self._learning_enabled = learning_enabled
        self._min_tool_calls = self._config.min_tool_calls_for_skill
        self._learning_interval = self._config.learning_interval
        self._stats = LearningStats()
        self._turn_counter = 0

        # 并发锁 — 防止 consolidation/reflection/decay 重入
        self._decay_lock = asyncio.Lock()
        self._consolidation_lock = asyncio.Lock()
        self._reflection_lock = asyncio.Lock()

    @property
    def stats(self) -> LearningStats:
        return self._stats

    async def on_turn_complete(
        self,
        messages: list[dict[str, Any]],
        result: Any,  # TurnResult
        user_id: str = "",
        model_router: Optional[Any] = None,
        matched_skill_id: str = "",
    ) -> dict[str, Any]:
        """每轮对话完成后的学习回调.

        Args:
            messages: 完整对话历史
            result: TurnResult (包含 content, tool_calls_made, duration_ms 等)
            user_id: 用户 ID
            model_router: ModelRouter 实例
            matched_skill_id: 本轮匹配到的技能 ID (用于 pipeline 级别学习)

        Returns:
            学习结果摘要 (包含: 记忆提取、技能提取、自动精炼、衰减、合并、反思等 actions)

        自动精炼逻辑 (step 3b):
            - use_count > 5 且 success_rate > 0.8 → 标记 "proven"，减少后续评估开销
            - use_count > 3 且 success_rate < 0.5 → 自动触发 optimize_skill
        """
        if not self._learning_enabled:
            return {"skipped": True, "reason": "learning_disabled"}

        self._stats.total_turns += 1
        self._turn_counter += 1

        learning_report: dict[str, Any] = {
            "turn": self._stats.total_turns,
            "actions": [],
        }

        # 1. 评估任务是否成功
        is_successful = self._evaluate_success(result)
        if is_successful:
            self._stats.successful_turns += 1

        # 2. 记忆提取 (每轮都做)
        if self._memory_manager:
            try:
                # 检查用户是否有明确的"记住"指令
                user_message = self._get_last_user_message(messages)
                assistant_response = result.content if result else ""

                await self._memory_manager.on_turn_complete(
                    user_message=user_message,
                    assistant_response=assistant_response,
                    user_id=user_id,
                    model_router=model_router,
                )

                # 按间隔自动提取记忆
                if self._turn_counter % self._learning_interval == 0 and model_router:
                    memory_ids = await self._memory_manager.extract_from_conversation(
                        messages=self._convert_messages(messages),
                        user_id=user_id,
                        model_router=model_router,
                    )
                    if memory_ids:
                        self._stats.memories_extracted += len(memory_ids)
                        learning_report["actions"].append({
                            "type": "memory_extraction",
                            "count": len(memory_ids),
                        })
            except (ValueError, TypeError, OSError) as e:
                logger.warning("Memory extraction in learning loop failed: %s", e)

        # 3. 技能提取 (仅成功的多工具任务)
        if (
            is_successful
            and self._skill_manager
            and model_router
            and result
            and hasattr(result, "tool_calls_made")
            and result.tool_calls_made >= self._min_tool_calls
        ):
            try:
                skill = await self._skill_manager.extract_from_conversation(
                    messages=self._convert_messages(messages),
                    model_router=model_router,
                )
                if skill:
                    # 自动提取的技能设为 draft，等待用户确认
                    skill.status = "draft"
                    skill.source = "auto_extracted"
                    await self._skill_manager._save_skill(skill)
                    self._stats.skills_created += 1
                    learning_report["actions"].append({
                        "type": "skill_created",
                        "skill_name": skill.name,
                        "skill_id": skill.skill_id,
                        "status": "draft",
                    })
                    # 通知信息会通过 learning_report 传递给前端
                    learning_report["notifications"] = learning_report.get("notifications", [])
                    learning_report["notifications"].append(
                        f"从对话中学到了新技能「{skill.name}」(draft)，"
                        f"说「激活技能 {skill.name}」来启用它。"
                    )
            except (ValueError, TypeError, OSError) as e:
                logger.warning("Skill extraction in learning loop failed: %s", e)

        # 3b. 自动技能精炼 — 成功率下降时触发优化，高成功率标记 proven
        if self._skill_manager and self._skill_optimizer and model_router:
            try:
                all_skills = await self._skill_manager.list_skills()
                for skill in all_skills:
                    if skill.use_count > 5 and skill.success_rate > 0.8:
                        if not skill.tags or "proven" not in skill.tags:
                            skill.tags = list(skill.tags or []) + ["proven"]
                            await self._skill_manager._save_skill(skill)
                            learning_report["actions"].append({
                                "type": "skill_marked_proven",
                                "skill_id": skill.skill_id,
                            })
                    elif skill.use_count > 3 and skill.success_rate < 0.5:
                        if "proven" not in (skill.tags or []):
                            await self._skill_optimizer.optimize_skill(
                                skill_id=skill.skill_id,
                                model_router=model_router,
                            )
                            self._stats.skills_refined += 1
                            learning_report["actions"].append({
                                "type": "skill_auto_refined",
                                "skill_id": skill.skill_id,
                                "success_rate": skill.success_rate,
                            })
            except (ValueError, TypeError, OSError) as e:
                logger.debug("Auto skill refinement skipped: %s", e)

        # 3c. Pipeline 级别学习 — 提取成功模式缓存到技能 metadata
        if (
            is_successful
            and matched_skill_id
            and self._skill_manager
            and model_router
        ):
            try:
                cache_updated = await self._extract_pipeline_metadata(
                    messages, matched_skill_id, model_router,
                )
                if cache_updated:
                    learning_report["actions"].append({
                        "type": "pipeline_cache_updated",
                        "skill_id": matched_skill_id,
                    })
            except (ValueError, TypeError, OSError) as e:
                logger.debug("Pipeline metadata extraction skipped: %s", e)

        # 4. 记忆衰减 (每 20 轮，带锁防重入)
        if self._memory_manager and self._turn_counter % self._config.decay_interval == 0:
            if not self._decay_lock.locked():
                asyncio.create_task(self._guarded_decay(user_id))
                learning_report["actions"].append({"type": "memory_decay_triggered"})
            else:
                logger.debug("Decay skipped: previous run still in progress")

        # 5. 记忆合并 (每 50 轮，带锁防重入)
        if self._memory_manager and self._turn_counter % self._config.consolidation_interval == 0 and model_router:
            if not self._consolidation_lock.locked():
                asyncio.create_task(self._guarded_consolidation(user_id, model_router))
                learning_report["actions"].append({"type": "consolidation_triggered"})
            else:
                logger.debug("Consolidation skipped: previous run still in progress")

        # 6. 自我反思 (每 100 轮，带锁防重入)
        if self._memory_manager and self._turn_counter % self._config.reflection_interval == 0 and model_router:
            if not self._reflection_lock.locked():
                asyncio.create_task(self._guarded_reflection(user_id, model_router))
                learning_report["actions"].append({"type": "reflection_triggered"})
            else:
                logger.debug("Reflection skipped: previous run still in progress")

        # 7. 记录学习时间
        if learning_report["actions"]:
            self._stats.last_learning_time = time.time()
            logger.info(
                "Learning loop: %d actions for turn %d",
                len(learning_report["actions"]),
                self._stats.total_turns,
            )

        return learning_report

    async def on_turn_failed(
        self,
        messages: list[dict[str, Any]],
        error_info: str,
        matched_skill_id: Optional[str] = None,
        failed_step: int = -1,
        user_id: str = "",
        model_router: Optional[Any] = None,
    ) -> dict[str, Any]:
        """任务失败后的学习回调.

        与 on_turn_complete 互补 — 从失败中学习。

        Args:
            messages: 对话历史
            error_info: 错误信息
            matched_skill_id: 如果使用了技能，传入技能 ID
            failed_step: 失败的步骤索引
            user_id: 用户 ID
            model_router: ModelRouter

        Returns:
            学习结果摘要
        """
        if not self._learning_enabled:
            return {"skipped": True, "reason": "learning_disabled"}

        self._stats.total_turns += 1
        self._stats.failed_turns += 1

        report: dict[str, Any] = {"turn": self._stats.total_turns, "actions": []}

        # 如果有匹配的技能，记录失败并尝试优化
        if matched_skill_id and self._skill_manager:
            await self._skill_manager.record_failure(matched_skill_id, error_info)
            report["actions"].append({
                "type": "skill_failure_recorded",
                "skill_id": matched_skill_id,
            })

            # 用优化器分析失败
            if self._skill_optimizer and model_router:
                try:
                    user_msg = self._get_last_user_message(messages)
                    record = await self._skill_optimizer.analyze_failure(
                        skill_id=matched_skill_id,
                        error_info=error_info,
                        user_message=user_msg,
                        failed_step=failed_step,
                        model_router=model_router,
                    )
                    if record and record.root_cause:
                        self._stats.skills_refined += 1
                        report["actions"].append({
                            "type": "skill_refined",
                            "skill_id": matched_skill_id,
                            "root_cause": record.root_cause,
                        })

                    # 检查是否应该废弃
                    deprecated = await self._skill_optimizer.check_deprecation(matched_skill_id)
                    if deprecated:
                        report["actions"].append({
                            "type": "skill_deprecated",
                            "skill_id": matched_skill_id,
                        })
                except (ValueError, TypeError, OSError) as e:
                    logger.warning("Failure analysis in learning loop failed: %s", e)

        if report["actions"]:
            self._stats.last_learning_time = time.time()
            logger.info("Learning from failure: %d actions", len(report["actions"]))

        return report

    async def _guarded_decay(self, user_id: str) -> None:
        """带锁的记忆衰减."""
        async with self._decay_lock:
            try:
                await self._memory_manager.decay_memories(user_id=user_id)
            except (ValueError, TypeError, OSError) as e:
                logger.warning("Guarded decay failed: %s", e)

    async def _guarded_consolidation(self, user_id: str, model_router: Any) -> None:
        """带锁的记忆合并."""
        async with self._consolidation_lock:
            try:
                await self._memory_manager.consolidate(user_id=user_id, model_router=model_router)
            except (ValueError, TypeError, OSError) as e:
                logger.warning("Guarded consolidation failed: %s", e)

    async def _guarded_reflection(self, user_id: str, model_router: Any) -> None:
        """带锁的自我反思."""
        async with self._reflection_lock:
            try:
                from agent.memory.reflector import MemoryReflector
                reflector = MemoryReflector(
                    self._memory_manager._provider, model_router=model_router
                )
                await reflector.reflect(user_id=user_id, model_router=model_router)
            except (ImportError, ValueError, TypeError, OSError) as e:
                logger.warning("Guarded reflection failed: %s", e)

    def _evaluate_success(self, result: Any) -> bool:
        """评估任务是否成功.

        简单启发式:
        - 有回复内容 = 成功
        - 回复中包含"错误"/"失败"/"Error" = 失败
        - 工具调用后有正常回复 = 成功
        """
        if not result or not hasattr(result, "content"):
            return False

        content = result.content or ""
        if not content:
            return False

        # 失败指标
        failure_keywords = [
            "error:", "failed:", "错误:", "失败:",
            "无法完成", "抱歉，我无法", "sorry, i can't",
        ]
        lower_content = content.lower()
        for keyword in failure_keywords:
            if keyword.lower() in lower_content:
                return False

        return True

    def _get_last_user_message(self, messages: list[Any]) -> str:
        """获取最后一条用户消息."""
        for msg in reversed(messages):
            if hasattr(msg, "role"):
                if msg.role == "user":
                    return msg.content if isinstance(msg.content, str) else ""
            elif isinstance(msg, dict):
                if msg.get("role") == "user":
                    return str(msg.get("content", ""))
        return ""

    def _convert_messages(self, messages: list[Any]) -> list[dict[str, Any]]:
        """将 Message 对象转换为 dict 列表."""
        result = []
        for msg in messages:
            if isinstance(msg, dict):
                result.append(msg)
            elif hasattr(msg, "role"):
                result.append({
                    "role": msg.role,
                    "content": msg.content if isinstance(msg.content, str) else str(msg.content),
                    "tool_calls": msg.tool_calls if hasattr(msg, "tool_calls") else None,
                })
        return result

    async def _extract_pipeline_metadata(
        self,
        messages: list[Any],
        skill_id: str,
        model_router: Any,
    ) -> bool:
        """从成功的 pipeline 执行中提取可复用模式.

        扫描对话中的工具调用，提取:
        - 成功的搜索查询词
        - 用户选择的设计方向
        - 成功的生成参数
        """
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return False

        converted = self._convert_messages(messages)
        queries: list[str] = []
        directions: list[str] = []
        gen_params: dict[str, Any] = {}

        for msg in converted:
            tool_calls = msg.get("tool_calls") or []
            if not isinstance(tool_calls, list):
                continue
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args_raw = fn.get("arguments", {})
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                elif isinstance(args_raw, dict):
                    args = args_raw
                else:
                    args = {}
                if not isinstance(args, dict):
                    args = {}

                if name == "web_search" and args.get("query"):
                    queries.append(args["query"])
                elif name == "generate_ecommerce_image":
                    gen_params = {
                        k: v for k, v in args.items()
                        if k in ("platform", "kind", "style", "description")
                    }

            # 提取 approval 工具的用户回复 (tool result messages)
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and content and not content.startswith(("[TIMEOUT]", "[EMPTY]", "[ERROR]")):
                    if len(content) < 200:
                        directions.append(content)

        if not queries and not gen_params:
            return False

        existing_cache = skill.metadata.get("pipeline_cache", {}) if skill.metadata else {}
        old_queries = existing_cache.get("successful_queries", [])
        old_dirs = existing_cache.get("preferred_directions", [])

        merged_queries = list(dict.fromkeys(old_queries + queries))[:10]

        dir_counts: dict[str, int] = {}
        for d in old_dirs:
            if isinstance(d, dict):
                dir_counts[d.get("label", "")] = d.get("chosen_count", 0)
        for d in directions:
            dir_counts[d] = dir_counts.get(d, 0) + 1
        merged_dirs = [
            {"label": k, "chosen_count": v}
            for k, v in sorted(dir_counts.items(), key=lambda x: -x[1])
        ][:5]

        new_cache = {
            "successful_queries": merged_queries,
            "preferred_directions": merged_dirs,
            "successful_params": gen_params or existing_cache.get("successful_params", {}),
            "last_updated": time.time(),
        }

        metadata = dict(skill.metadata) if isinstance(skill.metadata, dict) else {}
        metadata["pipeline_cache"] = new_cache
        await self._skill_manager.update_skill(skill_id, {"metadata": metadata})
        logger.info("Pipeline cache updated for skill %s", skill_id)
        return True

    @staticmethod
    def _format_pipeline_cache(cache: dict[str, Any]) -> str:
        """格式化 pipeline 缓存为 Markdown (控制在 500 字符内)."""
        parts: list[str] = []

        queries = cache.get("successful_queries", [])
        if queries:
            parts.append("**有效搜索词**: " + "、".join(queries[:3]))

        dirs = cache.get("preferred_directions", [])
        if dirs:
            top = [f"{d['label']}(选{d['chosen_count']}次)" for d in dirs[:3] if isinstance(d, dict)]
            if top:
                parts.append("**用户偏好方向**: " + "、".join(top))

        params = cache.get("successful_params", {})
        if params:
            p_str = "、".join(f"{k}={v}" for k, v in params.items() if v)
            if p_str:
                parts.append("**上次成功参数**: " + p_str)

        result = "\n".join(parts)
        return result[:500] if len(result) > 500 else result

    async def inject_context(
        self,
        user_message: str,
        user_id: str = "",
        model_router: Optional[Any] = None,
    ) -> InjectedContext:
        """在对话开始前注入学习到的上下文.

        返回 InjectedContext:
        - system_context: 记忆 + 技能概览 → 追加到 system prompt (稳定，prompt cache 友好)
        - skill_message: 匹配到的技能完整内容 → 作为 user message 前缀注入

        Returns:
            InjectedContext
        """
        result = InjectedContext()
        system_parts: list[str] = []

        # 注入记忆 → system prompt
        if self._memory_manager:
            try:
                memory_context, memory_ids = await self._memory_manager.get_memory_context(
                    user_message=user_message,
                    user_id=user_id,
                )
                if memory_context:
                    system_parts.append(memory_context)
                if memory_ids:
                    result.injected_memory_ids = memory_ids
            except (ValueError, TypeError, OSError) as e:
                logger.debug("Memory context injection failed: %s", e)

        # 注入技能概览 (tier 1) → system prompt
        if self._skill_manager:
            try:
                skills_prompt = self._skill_manager.get_skills_prompt(limit=5)
                if skills_prompt:
                    system_parts.append(skills_prompt)
            except (ValueError, TypeError) as e:
                logger.debug("技能概览注入跳过: %s", e)

        # Pinned 文件上下文注入
        if self._pin_manager:
            try:
                pinned_context = await self._pin_manager.get_pinned_context()
                if pinned_context:
                    system_parts.append(pinned_context)
            except (ValueError, TypeError, OSError) as e:
                logger.debug("Pinned 上下文注入跳过: %s", e)

        result.system_context = "\n".join(system_parts)

        # 安全上限: 注入的上下文不超过 8000 字符，防止 system prompt 膨胀
        if len(result.system_context) > 8000:
            result.system_context = result.system_context[:8000] + "\n...(上下文已截断)"

        # 匹配技能 (tier 2 完整内容) → user message
        if self._skill_manager:
            try:
                matched_skill = await self._skill_manager.match_skill(
                    user_message=user_message,
                    model_router=model_router,
                )
                if matched_skill:
                    # 校验工具可用性: 技能声明了 tools 但全部不存在 → 跳过注入
                    if (
                        self._tool_registry
                        and hasattr(matched_skill, 'tools')
                        and matched_skill.tools
                    ):
                        available = self._tool_registry.get_definitions_by_names(
                            matched_skill.tools
                        )
                        if not available:
                            logger.warning(
                                "Skill '%s' declares tools %s but none available, skipping",
                                matched_skill.name, matched_skill.tools,
                            )
                            matched_skill = None
                if matched_skill:
                    result.skill_message = matched_skill.to_full_content()
                    result.matched_skill_id = matched_skill.skill_id
                    await self._skill_manager.record_usage(matched_skill.skill_id)

                    # Pipeline 缓存注入 — 历史成功模式作为 Tier 3 上下文
                    if matched_skill.metadata and matched_skill.metadata.get("pipeline_cache"):
                        cache_text = self._format_pipeline_cache(
                            matched_skill.metadata["pipeline_cache"]
                        )
                        if cache_text:
                            result.skill_message += (
                                "\n\n## 历史成功模式（可直接复用）\n" + cache_text
                            )
            except (ValueError, TypeError, OSError) as e:
                logger.debug("Skill matching failed: %s", e)

        # 总注入量安全上限
        total_inject = len(result.system_context) + len(result.skill_message)
        if total_inject > 10000:
            result.skill_message = result.skill_message[:10000 - len(result.system_context)]

        return result

    def get_stats_summary(self) -> str:
        """获取学习统计摘要."""
        s = self._stats
        return (
            f"学习统计:\n"
            f"  总轮次: {s.total_turns}\n"
            f"  成功率: {s.successful_turns / max(s.total_turns, 1) * 100:.0f}%\n"
            f"  失败轮次: {s.failed_turns}\n"
            f"  已创建技能: {s.skills_created}\n"
            f"  已更新技能: {s.skills_updated}\n"
            f"  已优化技能: {s.skills_refined}\n"
            f"  已提取记忆: {s.memories_extracted}"
        )
