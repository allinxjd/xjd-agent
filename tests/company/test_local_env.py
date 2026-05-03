"""Tests for local environment detection."""

from __future__ import annotations

import platform

from agent.company.local_env import detect_local_env, format_env_for_context


class TestDetectLocalEnv:
    def test_returns_python_version(self):
        env = detect_local_env()
        assert env["python_version"] == platform.python_version()

    def test_returns_os(self):
        env = detect_local_env()
        assert "os" in env
        assert env["os"]

    def test_has_pip(self):
        env = detect_local_env()
        assert env.get("has_pip") == "true" or env.get("has_pip3") == "true"


class TestFormatEnvForContext:
    def test_contains_python_version(self):
        env = {"python_version": "3.9.6", "os": "Darwin 23.0.0 (arm64)"}
        result = format_env_for_context(env)
        assert "3.9.6" in result
        assert "用户本地开发环境" in result

    def test_includes_node_when_present(self):
        env = {"python_version": "3.11.0", "os": "Linux", "node_version": "v18.17.0"}
        result = format_env_for_context(env)
        assert "v18.17.0" in result

    def test_omits_node_when_absent(self):
        env = {"python_version": "3.11.0", "os": "Linux"}
        result = format_env_for_context(env)
        assert "Node" not in result

    def test_compat_hint_for_39(self):
        env = {"python_version": "3.9.6", "os": "Linux"}
        result = format_env_for_context(env)
        assert "match/case" in result
        assert "Optional" in result

    def test_no_compat_hint_for_312(self):
        env = {"python_version": "3.12.0", "os": "Linux"}
        result = format_env_for_context(env)
        assert "match/case" not in result

    def test_includes_package_managers(self):
        env = {"python_version": "3.11.0", "os": "Linux", "has_pip": "true", "has_npm": "true"}
        result = format_env_for_context(env)
        assert "pip" in result
        assert "npm" in result
