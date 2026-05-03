"""ChatBridge — 聊天平台抽象基类."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.company.environment import CompanyEnvironment
    from agent.company.message import CompanyMessage


class ChatBridge(ABC):
    """聊天平台 ↔ CompanyEnvironment 双向桥接抽象."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def mirror_to_chat(self, msg: CompanyMessage) -> None: ...

    @abstractmethod
    def set_environment(self, env: CompanyEnvironment) -> None: ...
