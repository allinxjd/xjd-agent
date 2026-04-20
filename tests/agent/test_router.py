"""测试 — ModelRouter 路由 + 故障转移."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.core.model_router import ModelRouter
from agent.providers.base import (
    CompletionResponse,
    Message,
    ProviderType,
    Usage,
)


class TestModelRouter:
    def test_init(self):
        router = ModelRouter()
        assert router._providers == {}

    def test_set_primary(self):
        router = ModelRouter()
        mock_provider = MagicMock()
        mock_provider.provider_type = ProviderType.OPENAI
        router.register_provider(mock_provider)
        router.set_primary("openai", "gpt-4o")
        assert router._primary_model == "gpt-4o"

    def test_set_cheap(self):
        router = ModelRouter()
        mock_provider = MagicMock()
        mock_provider.provider_type = ProviderType.OPENAI
        router.register_provider(mock_provider)
        router.set_cheap("openai", "gpt-4o-mini")
        assert router._cheap_model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_complete_with_failover(self):
        router = ModelRouter()

        mock_provider = AsyncMock()
        mock_provider.provider_type = ProviderType.OPENAI
        mock_provider.complete = AsyncMock(return_value=CompletionResponse(
            content="OK",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            model="gpt-4o",
            finish_reason="stop",
        ))

        router.register_provider(mock_provider)
        router.set_primary("openai", "gpt-4o")

        messages = [Message(role="user", content="test")]
        response = await router.complete_with_failover(
            messages=messages,
            user_message="test",
        )

        assert response.content == "OK"
        mock_provider.complete.assert_called_once()
