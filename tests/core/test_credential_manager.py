"""测试 — Credential 轮换管理."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agent.core.credential_manager import (
    CredentialManager,
    KeyState,
    ManagedCredential,
)


class TestManagedCredential:
    def test_masked_key(self):
        cred = ManagedCredential(api_key="sk-abcdefghijklmnop", provider="openai")
        assert "sk-a" in cred.masked_key
        assert "mnop" in cred.masked_key
        assert "***" in cred.masked_key

    def test_short_key_masked(self):
        cred = ManagedCredential(api_key="short", provider="test")
        assert cred.masked_key == "***"

    def test_default_state(self):
        cred = ManagedCredential(api_key="key", provider="test")
        assert cred.state == KeyState.ACTIVE


class TestCredentialManager:
    def test_add_key(self):
        cm = CredentialManager()
        cm.add_key("openai", "sk-111")
        assert cm.get_active_key("openai") == "sk-111"

    def test_add_keys(self):
        cm = CredentialManager()
        cm.add_keys("openai", ["sk-111", "sk-222"])
        keys = cm.list_keys("openai")
        assert len(keys) == 2

    def test_dedup(self):
        cm = CredentialManager()
        cm.add_key("openai", "sk-111")
        cm.add_key("openai", "sk-111")
        assert len(cm.list_keys("openai")) == 1

    def test_get_active_key_empty(self):
        cm = CredentialManager()
        assert cm.get_active_key("openai") is None

    def test_rotation_on_429(self):
        cm = CredentialManager()
        cm.add_keys("openai", ["sk-111", "sk-222"])
        cm.report_error("openai", "sk-111", 429)
        # sk-111 被限流，应返回 sk-222
        assert cm.get_active_key("openai") == "sk-222"

    def test_expiration_on_401(self):
        cm = CredentialManager()
        cm.add_keys("openai", ["sk-111", "sk-222"])
        cm.report_error("openai", "sk-111", 401)
        key = cm.get_active_key("openai")
        assert key == "sk-222"
        # sk-111 应为 EXPIRED
        cred = cm._find_credential("openai", "sk-111")
        assert cred.state == KeyState.EXPIRED

    def test_cooldown_recovery(self):
        cm = CredentialManager()
        cm.add_key("openai", "sk-111")
        cm.report_error("openai", "sk-111", 429)
        # 模拟冷却期已过
        cred = cm._find_credential("openai", "sk-111")
        cred.rate_limit_until = time.time() - 1
        assert cm.get_active_key("openai") == "sk-111"

    def test_all_keys_exhausted(self):
        cm = CredentialManager()
        cm.add_keys("openai", ["sk-111", "sk-222"])
        cm.report_error("openai", "sk-111", 401)
        cm.report_error("openai", "sk-222", 401)
        assert cm.get_active_key("openai") is None

    def test_report_success(self):
        cm = CredentialManager()
        cm.add_key("openai", "sk-111")
        cm.report_error("openai", "sk-111", 429)
        cred = cm._find_credential("openai", "sk-111")
        cred.rate_limit_until = time.time() - 1
        cm.get_active_key("openai")  # triggers recovery
        cm.report_success("openai", "sk-111")
        assert cred.fail_count == 0
        assert cred.total_requests == 1

    def test_get_stats(self):
        cm = CredentialManager()
        cm.add_keys("openai", ["sk-111", "sk-222", "sk-333"])
        cm.report_error("openai", "sk-111", 429)
        cm.report_error("openai", "sk-222", 401)
        stats = cm.get_stats()
        assert stats["openai"]["total"] == 3
        assert stats["openai"]["rate_limited"] == 1
        assert stats["openai"]["expired"] == 1
        assert stats["openai"]["active"] == 1

    def test_disable_enable(self):
        cm = CredentialManager()
        cm.add_key("openai", "sk-111")
        cm.disable_key("openai", "sk-111")
        assert cm.get_active_key("openai") is None
        cm.enable_key("openai", "sk-111")
        assert cm.get_active_key("openai") == "sk-111"

    def test_multiple_providers(self):
        cm = CredentialManager()
        cm.add_key("openai", "sk-111")
        cm.add_key("anthropic", "ant-222")
        assert cm.get_active_key("openai") == "sk-111"
        assert cm.get_active_key("anthropic") == "ant-222"
