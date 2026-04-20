"""Tests for sdk.client — SDK 客户端."""

import pytest
from sdk.client import XJDClient, ChatResponse, ToolResult, MemoryClient, AuthClient, AdminClient


class TestChatResponse:
    def test_defaults(self):
        r = ChatResponse()
        assert r.content == ""
        assert r.tool_calls == 0
        assert r.tokens == 0

    def test_with_data(self):
        r = ChatResponse(content="hello", tool_calls=3, tokens=150, duration_ms=500.0)
        assert r.content == "hello"
        assert r.tool_calls == 3


class TestToolResult:
    def test_success(self):
        r = ToolResult(tool="search", success=True, result="found 5 results")
        assert r.success
        assert r.tool == "search"

    def test_failure(self):
        r = ToolResult(tool="bad", success=False, error="not found")
        assert not r.success


class TestXJDClient:
    def test_init(self):
        client = XJDClient(base_url="http://example.com:8080", api_key="key123")
        assert client._base_url == "http://example.com:8080"
        assert client._api_key == "key123"
        assert isinstance(client.memory, MemoryClient)
        assert isinstance(client.auth, AuthClient)
        assert isinstance(client.admin, AdminClient)

    def test_embedded_mode(self):
        client = XJDClient.embedded()
        assert client._engine is None  # lazy init

    def test_headers(self):
        client = XJDClient(api_key="test-key")
        h = client._headers()
        assert h["Authorization"] == "Bearer test-key"
        assert h["Content-Type"] == "application/json"

    def test_headers_no_key(self):
        client = XJDClient()
        h = client._headers()
        assert "Authorization" not in h

    def test_sub_clients_share_parent(self):
        client = XJDClient(api_key="k")
        assert client.auth._client is client
        assert client.memory._client is client
        assert client.plugins._client is client
        assert client.admin._client is client
