"""CompanyEnvironment — 消息总线，路由 CompanyMessage 到订阅角色."""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole

logger = logging.getLogger(__name__)


class CompanyEnvironment:
    """角色间的消息总线."""

    def __init__(self) -> None:
        self._roles: dict[str, CompanyRole] = {}
        self._message_log: list[CompanyMessage] = []
        self._feishu_bridge: Any = None
        self._pipeline_user_queue: Optional[list[CompanyMessage]] = None

    def add_role(self, role: CompanyRole) -> None:
        self._roles[role.name] = role
        logger.info("环境注册角色: %s (watch=%s)", role.name, role.watch_actions)

    def remove_role(self, name: str) -> None:
        self._roles.pop(name, None)

    @property
    def roles(self) -> dict[str, CompanyRole]:
        return self._roles

    @property
    def message_log(self) -> list[CompanyMessage]:
        return self._message_log

    async def publish(self, msg: CompanyMessage) -> None:
        """发布消息到环境.

        路由规则：
        1. pipeline 运行中，HumanDirective 消息分流到 _pipeline_user_queue
        2. send_to 非空 → 定向投递
        3. send_to 空 → 广播到所有 watch_actions 匹配的角色
        """
        self._message_log.append(msg)
        logger.info("消息: %s", msg.summary())

        if (self._pipeline_user_queue is not None
                and msg.cause_by == "HumanDirective"
                and msg.sent_from not in self._roles):
            self._pipeline_user_queue.append(msg)
            if self._feishu_bridge:
                try:
                    await self._feishu_bridge.mirror_to_feishu(msg)
                except Exception as e:
                    logger.warning("飞书镜像失败: %s", e)
            return
        logger.info("消息: %s", msg.summary())

        delivered = False
        for role in self._roles.values():
            if msg.sent_from == role.name:
                continue
            if msg.send_to:
                if msg.send_to == role.name:
                    role.put_message(msg)
                    delivered = True
            elif msg.cause_by in role.watch_actions:
                role.put_message(msg)
                delivered = True

        if not delivered:
            logger.debug("消息未被任何角色接收: %s", msg.cause_by)

        if self._feishu_bridge:
            try:
                await self._feishu_bridge.mirror_to_feishu(msg)
            except Exception as e:
                logger.warning("飞书镜像失败: %s", e)

    def is_idle(self) -> bool:
        return all(not r.has_pending for r in self._roles.values())
