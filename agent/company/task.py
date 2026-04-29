"""CompanyTask — 带成功标准和验证循环的任务."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CompanyTask:
    """带验证循环的结构化任务."""

    task_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str = ""
    description: str = ""
    expected_output: str = ""
    assigned_to: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    max_retries: int = 2
    result: str = ""
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    async def verify(self, router: Any) -> tuple[bool, str]:
        """用 LLM 判断 result 是否满足 expected_output."""
        if not self.expected_output or not self.result:
            return True, ""

        prompt = (
            "判断以下任务结果是否满足预期输出标准。\n"
            "只回答 PASS 或 FAIL，如果 FAIL 请给出一句话修改建议。\n\n"
            f"## 预期标准\n{self.expected_output}\n\n"
            f"## 实际结果\n{self.result[:2000]}"
        )

        response = await router.chat(
            messages=[{"role": "user", "content": prompt}],
            intent="verify",
        )
        text = response.content if hasattr(response, "content") else str(response)

        if text.strip().startswith("PASS"):
            return True, ""
        feedback = text.replace("FAIL", "").strip().lstrip("：:").strip()
        return False, feedback
