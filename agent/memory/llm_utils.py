"""LLM JSON 调用工具 — 带重试和 JSON 解析."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Optional

from agent.memory.metrics import get_metrics

logger = logging.getLogger(__name__)


def _strip_markdown_json(content: str) -> str:
    """去掉 markdown code block 包裹."""
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1] if "\n" in content else content[3:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


async def llm_json_call(
    model_router,
    prompt: str,
    *,
    max_retries: int = 2,
    temperature: float = 0.3,
    backoff_base: float = 1.0,
) -> Optional[Any]:
    """调用 LLM 并解析 JSON 返回值，坏 JSON 时重试.

    Args:
        model_router: ModelRouter 实例
        prompt: 完整 prompt
        max_retries: JSON 解析失败时最大重试次数
        temperature: LLM 温度
        backoff_base: 指数退避基数 (秒)

    Returns:
        解析后的 JSON 对象，或 None (全部重试失败)
    """
    from agent.providers.base import Message as ProviderMessage
    m = get_metrics()

    last_error = None
    for attempt in range(1 + max_retries):
        try:
            m.llm_calls += 1
            t0 = time.monotonic()
            response = await model_router.complete_with_failover(
                messages=[ProviderMessage(role="user", content=prompt)],
                user_message=prompt,
                temperature=temperature,
            )
            m.record_latency("llm_call", (time.monotonic() - t0) * 1000)

            content = _strip_markdown_json(response.content)
            result = json.loads(content)
            return result

        except json.JSONDecodeError as e:
            last_error = e
            m.llm_retries += 1
            if attempt < max_retries:
                wait = backoff_base * (2 ** attempt)
                logger.debug(
                    "LLM returned invalid JSON (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, 1 + max_retries, wait, str(e)[:80],
                )
                await asyncio.sleep(wait)
            else:
                m.llm_failures += 1
                logger.warning(
                    "LLM JSON call failed after %d attempts: %s",
                    1 + max_retries, str(e)[:80],
                )
        except (OSError, RuntimeError) as e:
            m.llm_failures += 1
            logger.warning("LLM call failed (non-retryable): %s", e)
            return None

    return None
