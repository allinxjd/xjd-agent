"""测试 — Config 配置系统."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent.core.config import Config, ProviderConfig, get_home


class TestConfig:
    def test_default_config(self):
        config = Config()
        assert config.model is not None
        assert config.model.primary is not None

    def test_provider_config(self):
        pc = ProviderConfig(
            provider="openai",
            model="gpt-4o",
            api_key="sk-test",
        )
        assert pc.provider == "openai"
        assert pc.model == "gpt-4o"

    def test_load_from_yaml(self, tmp_dir):
        config_file = tmp_dir / "config.yaml"
        config_file.write_text("""
model:
  primary:
    provider: deepseek
    model: deepseek-chat
    api_key: sk-xxx
""")

        os.environ["XJD_HOME"] = str(tmp_dir)
        try:
            config = Config.load()
            assert config.model.primary.provider == "deepseek"
            assert config.model.primary.model == "deepseek-chat"
        finally:
            os.environ.pop("XJD_HOME", None)

    def test_env_overrides(self):
        config = Config()
        os.environ["XJD_PRIMARY_PROVIDER"] = "anthropic"
        os.environ["XJD_PRIMARY_MODEL"] = "claude-sonnet"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"

        try:
            config.apply_env_overrides()
            assert config.model.primary.provider == "anthropic"
            assert config.model.primary.model == "claude-sonnet"
        finally:
            os.environ.pop("XJD_PRIMARY_PROVIDER", None)
            os.environ.pop("XJD_PRIMARY_MODEL", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)

    def test_get_home_default(self):
        old = os.environ.pop("XJD_HOME", None)
        try:
            home = get_home()
            assert home == Path.home() / ".xjd-agent"
        finally:
            if old:
                os.environ["XJD_HOME"] = old
