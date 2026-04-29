"""CompanyMessage — 带 cause_by 路由的结构化消息."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompanyMessage:
    """角色间传递的结构化消息.

    cause_by 用于路由：角色通过 watch_actions 订阅特定 Action 产生的消息。
    """

    content: str
    cause_by: str = ""
    sent_from: str = ""
    send_to: str = ""
    task_id: str = ""
    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def summary(self, max_len: int = 200) -> str:
        """用于日志和飞书展示的摘要."""
        body = self.content[:max_len]
        if len(self.content) > max_len:
            body += "..."
        return f"[{self.sent_from}→{self.send_to or '*'}] ({self.cause_by}) {body}"
