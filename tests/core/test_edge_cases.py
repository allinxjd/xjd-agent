"""
补充边界测试 — 覆盖代码审查发现的潜在风险点。

覆盖范围:
  1. design_fetcher: GitHub API 返回 None 字段时的防御性处理
  2. skill_fetcher: 同上
  3. design_fetcher: API 失败时返回空列表（无 fallback 行为验证）
  4. skill_fetcher: 同上
  5. scheduler: 验证导入路径正确性（防止死代码回归）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ============================================================================
# 1. design_fetcher — None 字段防御
# ============================================================================

class TestDesignFetcherNoneDefense:
    """验证 fetch_design_projects 对 GitHub API 返回 None 字段的防御。"""

    def _call_fetch(self, raw_data, monkeypatch):
        monkeypatch.setattr(
            "src.design_fetcher.search_github_repos",
            lambda *a, **kw: raw_data,
        )
        from src.design_fetcher import fetch_design_projects
        return fetch_design_projects()

    def test_name_is_none(self, monkeypatch):
        """name 字段为 None 时不抛异常，org 提取回退为空字符串。"""
        raw = [{
            "name": None,
            "description": "desc",
            "stars": 2000,
            "language": "Python",
            "forks": 10,
            "url": "https://github.com/test/repo",
        }]
        # 当前代码: repo.get("name", "") 对 None 返回 None
        # 然后 "/" in None → TypeError
        # 这是一个已知边界行为，验证其表现
        try:
            result = self._call_fetch(raw, monkeypatch)
            # 如果没抛异常，验证结果
            assert len(result) == 1
            assert result[0]["is_recognized_by_expert"] is True  # stars > 1000
        except TypeError:
            pytest.fail("name=None 导致 TypeError，建议在 fetch_design_projects 中增加防御")

    def test_description_is_none(self, monkeypatch):
        """description 为 None 时填充空字符串。"""
        raw = [{
            "name": "owner/repo",
            "description": None,
            "stars": 0,
            "language": "Python",
            "forks": 10,
            "url": "https://github.com/owner/repo",
        }]
        result = self._call_fetch(raw, monkeypatch)
        assert result[0]["description"] == ""

    def test_language_is_none(self, monkeypatch):
        """language 为 None 时填充空字符串。"""
        raw = [{
            "name": "owner/repo",
            "description": "desc",
            "stars": 0,
            "language": None,
            "forks": 10,
            "url": "https://github.com/owner/repo",
        }]
        result = self._call_fetch(raw, monkeypatch)
        assert result[0]["language"] == ""

    def test_stars_is_none(self, monkeypatch):
        """stars 为 None 时填充 0。"""
        raw = [{
            "name": "owner/repo",
            "description": "desc",
            "stars": None,
            "language": "Python",
            "forks": 10,
            "url": "https://github.com/owner/repo",
        }]
        result = self._call_fetch(raw, monkeypatch)
        assert result[0]["stars"] == 0

    def test_all_optional_fields_none(self, monkeypatch):
        """所有可选字段为 None 时全部填充安全默认值。"""
        raw = [{
            "name": "owner/repo",
            "description": None,
            "stars": None,
            "language": None,
            "forks": None,
            "url": None,
        }]
        result = self._call_fetch(raw, monkeypatch)
        r = result[0]
        assert r["description"] == ""
        assert r["stars"] == 0
        assert r["language"] == ""
        assert r["forks"] == 0
        assert r["url"] == ""

    def test_missing_all_keys_except_name(self, monkeypatch):
        """只有 name 字段时的最小响应。"""
        raw = [{"name": "minimal/repo"}]
        result = self._call_fetch(raw, monkeypatch)
        r = result[0]
        assert r["name"] == "minimal/repo"
        assert r["description"] == ""
        assert r["stars"] == 0
        assert r["language"] == ""
        assert r["forks"] == 0
        assert r["url"] == ""


# ============================================================================
# 2. skill_fetcher — None 字段防御
# ============================================================================

class TestSkillFetcherNoneDefense:
    """验证 fetch_skill_projects 对 None 字段的防御。"""

    def _call_fetch(self, raw_data, monkeypatch):
        monkeypatch.setattr(
            "src.skill_fetcher.search_github_repos",
            lambda *a, **kw: raw_data,
        )
        from src.skill_fetcher import fetch_skill_projects
        return fetch_skill_projects()

    def test_name_is_none(self, monkeypatch):
        """name 为 None 时验证防御行为。"""
        raw = [{
            "name": None,
            "description": "desc",
            "stars": 2000,
            "language": "Python",
            "forks": 10,
            "url": "https://github.com/test/repo",
        }]
        try:
            result = self._call_fetch(raw, monkeypatch)
            assert len(result) == 1
            assert result[0]["is_recognized_by_expert"] is True
        except TypeError:
            pytest.fail("name=None 导致 TypeError，建议增加防御")

    def test_all_optional_fields_none(self, monkeypatch):
        """所有可选字段为 None 时填充安全默认值。"""
        raw = [{
            "name": "owner/repo",
            "description": None,
            "stars": None,
            "language": None,
            "forks": None,
            "url": None,
        }]
        result = self._call_fetch(raw, monkeypatch)
        r = result[0]
        assert r["description"] == ""
        assert r["stars"] == 0
        assert r["language"] == ""
        assert r["forks"] == 0
        assert r["url"] == ""


# ============================================================================
# 3. API 失败时返回空列表（无 fallback）
# ============================================================================

class TestFetcherNoFallbackBehavior:
    """验证当前版本 API 失败时返回空列表（不依赖 fallback 数据）。"""

    def test_design_fetcher_returns_empty_on_failure(self, monkeypatch):
        """GitHub API 异常 → 返回 []（当前版本无 fallback）。"""
        monkeypatch.setattr(
            "src.design_fetcher.search_github_repos",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API down")),
        )
        from src.design_fetcher import fetch_design_projects
        result = fetch_design_projects()
        assert result == []

    def test_skill_fetcher_returns_empty_on_failure(self, monkeypatch):
        """GitHub API 异常 → 返回 []（当前版本无 fallback）。"""
        monkeypatch.setattr(
            "src.skill_fetcher.search_github_repos",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("API down")),
        )
        from src.skill_fetcher import fetch_skill_projects
        result = fetch_skill_projects()
        assert result == []


# ============================================================================
# 4. scheduler 导入路径验证（防止死代码回归）
# ============================================================================

class TestSchedulerImportPaths:
    """验证 scheduler._refresh_all 从正确模块导入（防止代码审查中发现的死代码问题回归）。"""

    def test_imports_from_fetch_pipeline(self):
        """scheduler 从 src.fetch_pipeline 导入 refresh_all。"""
        import importlib
        import src.scheduler as sched
        importlib.reload(sched)

        import inspect
        source = inspect.getsource(sched._refresh_all)
        assert "from src.fetch_pipeline import" in source, \
            "scheduler 应从 src.fetch_pipeline 导入"
        assert "from src.fetchers" not in source, \
            "scheduler 不应从已删除的 src.fetchers 子包导入"
