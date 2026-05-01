"""CompanyRole — 扩展 AgentRole，增加 observe/think/act 生命周期."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.company.action import Action
from agent.company.message import CompanyMessage
from agent.core.multi_agent import AgentRole

logger = logging.getLogger(__name__)


def _clean_model_artifacts(text: str) -> str:
    """Strip LLM-specific markup (DeepSeek DSML, tool call tags) from output."""
    text = re.sub(r'<\uff5c\uff5cDSML\uff5c\uff5c[^>]*>.*?(?:</\uff5c\uff5cDSML\uff5c\uff5c[^>]*>|$)', '', text, flags=re.DOTALL)
    text = re.sub(r'</?\uff5c\uff5c[^>]*>', '', text)
    text = re.sub(r'</?antml:[a-z_]+[^>]*>', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


@dataclass
class CompanyRole(AgentRole):
    """AI 公司中的角色.

    继承 AgentRole 的全部字段，增加：
    - CrewAI 三元组 (goal, backstory)
    - MetaGPT 生命周期 (observe → think → act)
    - Karpathy 行为约束
    """

    goal: str = ""
    backstory: str = ""
    watch_actions: list[str] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    react_mode: str = "by_order"
    karpathy_constraints: list[str] = field(default_factory=list)
    feishu_bot_app_id: str = ""

    _inbox: list[CompanyMessage] = field(default_factory=list, repr=False)
    _state: int = field(default=-1, repr=False)
    _runtime_router: Any = field(default=None, repr=False)
    _runtime_registry: Any = field(default=None, repr=False)
    _processed_msg_ids: set = field(default_factory=set, repr=False)

    def put_message(self, msg: CompanyMessage) -> None:
        self._inbox.append(msg)

    @property
    def has_pending(self) -> bool:
        return len(self._inbox) > 0

    async def _observe(self) -> list[CompanyMessage]:
        """从 inbox 读取消息，按 watch_actions 过滤，跳过已处理的."""
        matched = []
        remaining = []
        for msg in self._inbox:
            if msg.msg_id in self._processed_msg_ids:
                continue
            if msg.send_to == self.name or msg.cause_by in self.watch_actions:
                matched.append(msg)
                self._processed_msg_ids.add(msg.msg_id)
            else:
                remaining.append(msg)
        self._inbox = remaining
        return matched

    async def _think(self, messages: list[CompanyMessage]) -> Optional[Action]:
        """选择下一个要执行的 Action."""
        if not self.actions:
            return None
        if self.react_mode == "by_order":
            self._state += 1
            if self._state < len(self.actions):
                return self.actions[self._state]
            return None
        if self.react_mode == "react" and self._runtime_router:
            return await self._think_react(messages)
        return self.actions[0]

    async def _think_react(self, messages: list[CompanyMessage]) -> Optional[Action]:
        """用 LLM 动态选择下一个 Action."""
        context = "\n".join(m.content[:200] for m in messages[-3:])
        action_list = "\n".join(
            f"- {a.name}: {a.description}" for a in self.actions
        )
        prompt = (
            f"你是 {self.name}，根据当前上下文选择下一步要执行的操作。\n"
            f"只回答操作名称，不要解释。如果所有操作都已完成，回答 DONE。\n\n"
            f"## 可选操作\n{action_list}\n\n"
            f"## 当前上下文\n{context[:500]}"
        )
        try:
            response = await self._runtime_router.chat(
                messages=[{"role": "user", "content": prompt}],
                intent="think",
            )
            text = response.content if hasattr(response, "content") else str(response)
            choice = text.strip().split("\n")[0].strip()
            if choice == "DONE":
                return None
            for action in self.actions:
                if action.name == choice or action.name.lower() == choice.lower():
                    return action
        except Exception as e:
            logger.warning("[%s] react 模式 LLM 选择失败: %s，回退 by_order", self.name, e)
        self._state += 1
        if self._state < len(self.actions):
            return self.actions[self._state]
        return None

    async def _act(self, action: Action, context: str) -> CompanyMessage:
        """执行 Action，返回结果消息."""
        logger.info("[%s] 执行 %s", self.name, action.name)
        content = await action.run(context, self)
        content = _clean_model_artifacts(content)
        if not content:
            content = f"[{self.name}] {action.name} 已执行完毕，但未产生有效输出。"
        return CompanyMessage(
            content=content,
            cause_by=action.name,
            sent_from=self.name,
        )

    async def run(self) -> Optional[CompanyMessage]:
        """主入口：observe → think → act."""
        messages = await self._observe()
        if not messages:
            return None

        context = "\n\n---\n\n".join(m.content for m in messages)
        self._state = -1
        last_msg: Optional[CompanyMessage] = None

        while True:
            action = await self._think(messages)
            if action is None:
                break
            last_msg = await self._act(action, context)
            context = last_msg.content

        return last_msg

    def build_system_prompt(self) -> str:
        """组合 system prompt: 基础 + goal/backstory + Karpathy 约束."""
        parts = []
        if self.system_prompt:
            parts.append(self.system_prompt)
        if self.goal:
            parts.append(f"## 你的目标\n{self.goal}")
        if self.backstory:
            parts.append(f"## 你的背景\n{self.backstory}")
        if self.karpathy_constraints:
            parts.append("## 行为准则\n" + "\n".join(
                f"- {c}" for c in self.karpathy_constraints
            ))
        return "\n\n".join(parts)
