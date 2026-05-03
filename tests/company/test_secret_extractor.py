"""Tests for secret extraction from chat history."""

from __future__ import annotations

from pathlib import Path

from agent.company.secret_extractor import extract_secrets, write_env_file


class TestExtractSecrets:
    def test_github_token_with_context(self):
        history = [("user", "我的 github token 是 ghp_abcdefghijklmnopqrstuvwxyz1234")]
        result = extract_secrets(history)
        assert "GITHUB_TOKEN" in result
        assert result["GITHUB_TOKEN"].startswith("ghp_")

    def test_github_token_bare(self):
        history = [("user", "用这个 ghp_abcdefghijklmnopqrstuvwxyz1234")]
        result = extract_secrets(history)
        assert "GITHUB_TOKEN" in result

    def test_openai_key(self):
        history = [("user", "openai key: sk-abc123def456ghi789jkl012mno")]
        result = extract_secrets(history)
        assert "OPENAI_API_KEY" in result
        assert result["OPENAI_API_KEY"].startswith("sk-")

    def test_anthropic_key(self):
        history = [("user", "sk-ant-abcdefghijklmnopqrstuvwxyz")]
        result = extract_secrets(history)
        assert "ANTHROPIC_API_KEY" in result

    def test_generic_api_key(self):
        history = [("user", "api_key=abcdefghijklmnopqrstuvwxyz123")]
        result = extract_secrets(history)
        assert "API_KEY" in result

    def test_no_secrets(self):
        history = [("user", "帮我写一个商城"), ("PM", "收到")]
        result = extract_secrets(history)
        assert result == {}

    def test_short_strings_ignored(self):
        history = [("user", "api_key=short")]
        result = extract_secrets(history)
        assert result == {}

    def test_specific_takes_priority(self):
        history = [("user", "openai api key: sk-abc123def456ghi789jkl012mno")]
        result = extract_secrets(history)
        assert "OPENAI_API_KEY" in result
        assert "API_KEY" not in result


class TestWriteEnvFile:
    def test_creates_env_file(self, tmp_path: Path):
        result = write_env_file(tmp_path, {"API_KEY": "test123_abcdefghijklmnop"})
        assert result is not None
        content = (tmp_path / ".env").read_text()
        assert "API_KEY=test123_abcdefghijklmnop" in content

    def test_no_secrets_returns_none(self, tmp_path: Path):
        result = write_env_file(tmp_path, {})
        assert result is None

    def test_appends_to_existing(self, tmp_path: Path):
        (tmp_path / ".env").write_text("EXISTING=value\n")
        write_env_file(tmp_path, {"NEW_KEY": "new_value"})
        content = (tmp_path / ".env").read_text()
        assert "EXISTING=value" in content
        assert "NEW_KEY=new_value" in content

    def test_no_duplicate_keys(self, tmp_path: Path):
        (tmp_path / ".env").write_text("API_KEY=old_value\n")
        write_env_file(tmp_path, {"API_KEY": "new_value"})
        content = (tmp_path / ".env").read_text()
        assert content.count("API_KEY=") == 1
