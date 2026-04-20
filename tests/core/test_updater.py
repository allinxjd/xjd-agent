"""测试 — 自动更新."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from agent.core.updater import (
    compare_versions,
    get_current_version,
    PACKAGE_NAME,
)


class TestCompareVersions:
    def test_newer(self):
        assert compare_versions("0.1.0", "0.2.0") is True

    def test_same(self):
        assert compare_versions("1.0.0", "1.0.0") is False

    def test_older(self):
        assert compare_versions("2.0.0", "1.0.0") is False

    def test_patch_update(self):
        assert compare_versions("1.0.0", "1.0.1") is True

    def test_major_update(self):
        assert compare_versions("1.9.9", "2.0.0") is True

    def test_v_prefix(self):
        assert compare_versions("v1.0.0", "v1.1.0") is True

    def test_pre_release(self):
        assert compare_versions("1.0.0-beta", "1.0.1") is True


class TestGetCurrentVersion:
    def test_returns_string(self):
        version = get_current_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_version_format(self):
        version = get_current_version()
        parts = version.split(".")
        assert len(parts) >= 2  # at least major.minor


class TestPackageName:
    def test_package_name(self):
        assert PACKAGE_NAME == "xjd-agent"
