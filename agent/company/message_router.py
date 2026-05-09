"""MessageCoordinator — 基于意图分类的消息路由协调器.

在 pipeline 运行期间，对用户消息进行分类并决定路由策略：
- supplement: 注入当前阶段上下文
- question: 路由给 PM 立即回答（不阻塞 pipeline）
- confirm: 路由到 approval handler
- cancel: 设置 pipeline_cancel
- new_task: 暂存，pipeline 结束后处理
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.company.environment import CompanyEnvironment
    from agent.company.intent_classifier import ClassificationResult, IntentClassifier
    from agent.company.message import CompanyMessage

logger = logging.getLogger(__name__)


class MessagePriority(IntEnum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class RoutingDecision:
    """消息路由决策."""

    action: str  # inject_supplement | answer_question | route_approval | cancel_pipeline | queue_new_task | ignore
    target_role: str = ""
    priority: MessagePriority = MessagePriority.NORMAL
    classification: Optional["ClassificationResult"] = None


class MessageCoordinator:
    """协调 pipeline 运行期间的用户消息路由."""

    def __init__(
        self,
        classifier: "IntentClassifier",
        env: Optional["CompanyEnvironment"] = None,
    ) -> None:
        self._classifier = classifier
        self._env = env
        self._pending_new_tasks: list["CompanyMessage"] = []

    @property
    def pending_new_tasks(self) -> list["CompanyMessage"]:
        """Pipeline 结束后需要处理的新任务消息."""
        return self._pending_new_tasks

    def clear_pending(self) -> None:
        self._pending_new_tasks.clear()

    async def route_user_message(
        self,
        msg: "CompanyMessage",
        pipeline_state: str = "running",
        waiting_approval: bool = False,
    ) -> RoutingDecision:
        """分类并路由用户消息.

        Args:
            msg: 用户消息
            pipeline_state: 当前 pipeline 状态描述
            waiting_approval: 是否正在等待用户审批
        """
        text = msg.content.strip()
        if not text:
            return RoutingDecision(action="ignore")

        if waiting_approval:
            result = self._classifier.classify_sync(text)
            if result.intent == "confirm":
                return RoutingDecision(
                    action="route_approval",
                    priority=MessagePriority.URGENT,
                    classification=result,
                )
            if result.intent == "cancel":
                return RoutingDecision(
                    action="cancel_pipeline",
                    priority=MessagePriority.URGENT,
                    classification=result,
                )
            return RoutingDecision(
                action="route_approval",
                priority=MessagePriority.HIGH,
                classification=result,
            )

        result = await self._classifier.classify(text, pipeline_state)

        if result.intent == "cancel":
            return RoutingDecision(
                action="cancel_pipeline",
                priority=MessagePriority.URGENT,
                classification=result,
            )

        if result.intent == "confirm":
            return RoutingDecision(
                action="route_approval",
                priority=MessagePriority.HIGH,
                classification=result,
            )

        if result.intent == "question":
            return RoutingDecision(
                action="answer_question",
                target_role="PM",
                priority=MessagePriority.NORMAL,
                classification=result,
            )

        if result.intent == "new_task":
            if result.confidence >= 0.8:
                self._pending_new_tasks.append(msg)
                return RoutingDecision(
                    action="queue_new_task",
                    priority=MessagePriority.LOW,
                    classification=result,
                )
            return RoutingDecision(
                action="inject_supplement",
                target_role="",
                priority=MessagePriority.NORMAL,
                classification=result,
            )

        return RoutingDecision(
            action="inject_supplement",
            target_role="",
            priority=MessagePriority.NORMAL,
            classification=result,
        )
