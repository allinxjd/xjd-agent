"""
测试 — fetch_pipeline: 数据管线核心模块。

覆盖范围:
  1. get_source_label — 来源标签映射与未匹配回退
  2. _maybe_translate — 直通原文
  3. _enrich_news — title_cn / summary_cn / source_label 附加
  4. _enhance_and_translate_projects — title_cn / summary_cn / source_label / interpretation 附加
  4. refresh_all — 全量刷新正常路径、部分失败、全部失败
  5. get_ai_news / get_skill_projects / get_design_projects — 缓存读取 + 兜底空列表
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.fetch_pipeline import (
    _enrich_news,
    _enhance_and_translate_projects,
    refresh_all,
    get_ai_news,
    get_skill_projects,
    get_design_projects,
)
from src.source_label_mapper import get_source_label, SOURCE_LABEL_MAP


# ============================================================================
# 1. get_source_label — 来源标签映射
# ============================================================================

class TestSourceLabel:
    """验证 get_source_label 的来源标签映射与回退。"""

    def test_matches_openai(self):
        assert get_source_label("https://openai.com/blog") == "OpenAI 官方"

    def test_matches_huggingface(self):
        assert get_source_label("huggingface.co/blog/feed.xml") == "HuggingFace"

    def test_matches_mit_tech_review(self):
        assert get_source_label("https://www.technologyreview.com/feed/") == "MIT Tech Review"

    def test_matches_github(self):
        assert get_source_label("github.com/owner/repo") == "GitHub"

    def test_case_insensitive_match(self):
        """域名匹配不区分大小写。"""
        assert get_source_label("OpenAI.com") == "OpenAI 官方"
        assert get_source_label("HUGGINGFACE.CO") == "HuggingFace"

    def test_partial_match_in_url(self):
        """子字符串匹配：含 key 即命中。"""
        assert get_source_label("https://sub.openai.com/path") == "OpenAI 官方"

    def test_no_match_returns_original(self):
        """未匹配任何 key 时返回原始值。"""
        assert get_source_label("https://example.com") == "https://example.com"
        assert get_source_label("unknown-blog.io") == "unknown-blog.io"

    def test_empty_source(self):
        """空字符串回退为未知来源。"""
        assert get_source_label("") == "未知来源"

    def test_first_match_wins(self):
        """如果有重叠 key，第一个匹配的生效。
        
        SOURCE_LABEL_MAP 按定义顺序匹配。
        """
        # technologyreview.com 同时匹配 technologyreview.com
        result = get_source_label("https://www.technologyreview.com/ai")
        assert result == "MIT Tech Review"

    def test_all_map_entries_covered(self):
        """SOURCE_LABEL_MAP 中每条都有对应的 source_label。"""
        for key, label in SOURCE_LABEL_MAP.items():
            assert isinstance(key, str) and len(key) > 0
            assert isinstance(label, str) and len(label) > 0


# ============================================================================

# ============================================================================

# ============================================================================
# 2. _enrich_news — 新闻条目增强
# ============================================================================

class TestEnrichNews:
    """验证 _enrich_news 为新闻条目设置 title_cn / summary_cn / source_label。"""

    def test_adds_all_three_fields(self):
        entries = [{
            "title": "AI Breakthrough",
            "summary": "A big leap in AI.",
            "source": "https://openai.com/blog",
            "link": "https://openai.com/blog/1",
            "published": "2026-05-01",
        }]
        result = _enrich_news(entries)
        e = result[0]
        assert e["title_cn"] == "AI Breakthrough"
        assert e["summary_cn"] == "A big leap in AI."
        assert e["source_label"] == "OpenAI 官方"

    def test_preserves_existing_fields(self):
        """已有 title_cn 等字段时 setdefault 不覆盖。"""
        entries = [{
            "title": "Original",
            "summary": "Summary",
            "source": "unknown",
            "title_cn": "已翻译标题",
            "summary_cn": "已翻译摘要",
            "source_label": "自定义标签",
        }]
        result = _enrich_news(entries)
        e = result[0]
        assert e["title_cn"] == "已翻译标题"
        assert e["summary_cn"] == "已翻译摘要"
        assert e["source_label"] == "自定义标签"

    def test_missing_title(self):
        """缺少 title 时 title_cn 为空字符串。"""
        entries = [{"summary": "S", "source": "test"}]
        result = _enrich_news(entries)
        assert result[0]["title_cn"] == ""

    def test_empty_list(self):
        """空列表直接返回。"""
        assert _enrich_news([]) == []

    def test_multiple_entries(self):
        """多个条目全部正确增强。"""
        entries = [
            {"title": "T1", "summary": "S1", "source": "openai.com"},
            {"title": "T2", "summary": "S2", "source": "huggingface.co"},
        ]
        result = _enrich_news(entries)
        assert len(result) == 2
        assert result[0]["source_label"] == "OpenAI 官方"
        assert result[1]["source_label"] == "HuggingFace"


# ============================================================================
# 4. _enhance_and_translate_projects — 项目条目增强
# ============================================================================

class TestEnrichProjects:
    """验证 _enhance_and_translate_projects 对 skill/design 项目的字段增强。"""

    def test_enrich_skill_projects(self):
        projects = [{
            "name": "microsoft/vscode",
            "description": "VS Code editor",
        }]
        result = _enhance_and_translate_projects(projects, "skill")
        p = result[0]
        assert p["title_cn"] == "microsoft/vscode"
        assert p["summary_cn"] == "VS Code editor"
        assert p["source_label"] == "GitHub"
        assert "skill_interpretation" in p
        assert p["skill_interpretation"] == ""

    def test_enrich_design_projects(self):
        projects = [{
            "name": "shadcn/ui",
            "description": "UI components",
        }]
        result = _enhance_and_translate_projects(projects, "design")
        p = result[0]
        assert p["title_cn"] == "shadcn/ui"
        assert p["summary_cn"] == "UI components"
        assert p["source_label"] == "GitHub"
        assert "design_interpretation" in p
        assert p["design_interpretation"] == ""

    def test_preserves_existing_interpretation(self):
        """已有 interpretation 时不被覆盖。"""
        projects = [{
            "name": "test/repo",
            "description": "desc",
            "skill_interpretation": "已有解读",
        }]
        result = _enhance_and_translate_projects(projects, "skill")
        assert result[0]["skill_interpretation"] == "已有解读"

    def test_preserves_existing_title_cn(self):
        """已有 title_cn 不被覆盖。"""
        projects = [{
            "name": "test/repo",
            "description": "desc",
            "title_cn": "已翻译名",
        }]
        result = _enhance_and_translate_projects(projects, "skill")
        assert result[0]["title_cn"] == "已翻译名"

    def test_empty_name(self):
        """空 name → title_cn 为空字符串。"""
        projects = [{"name": "", "description": ""}]
        result = _enhance_and_translate_projects(projects, "skill")
        assert result[0]["title_cn"] == ""

    def test_missing_description(self):
        """缺少 description → summary_cn 为空字符串。"""
        projects = [{"name": "test/repo"}]
        result = _enhance_and_translate_projects(projects, "skill")
        assert result[0]["summary_cn"] == ""

    def test_empty_list(self):
        assert _enhance_and_translate_projects([], "skill") == []
        assert _enhance_and_translate_projects([], "design") == []


# ============================================================================
# 4. refresh_all — 全量刷新
# ============================================================================

class TestRefreshAll:
    """验证 refresh_all 的正常路径、部分失败、全部失败。"""

    # --- 正常路径 ---

    def test_all_three_succeed(self, monkeypatch, tmp_path):
        """三个板块全部成功刷新。"""
        # Mock 数据
        mock_news = [
            {"title": "News 1", "link": "a", "summary": "S", "source": "X", "published": "2026-05-01"},
        ]
        mock_skills = [
            {"name": "a/b", "description": "desc", "stars": 100, "language": "Py", "forks": 10,
             "url": "https://github.com/a/b"},
        ]
        mock_designs = [
            {"name": "c/d", "description": "desc", "stars": 200, "language": "TS", "forks": 20,
             "url": "https://github.com/c/d"},
        ]

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", lambda: mock_news)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: mock_skills)
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: mock_designs)

        # Mock save 函数
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        result = refresh_all()

        assert result["ai_news"] == 1
        assert result["skill_projects"] == 1
        assert result["design_projects"] == 1

    def test_enriched_fields_present_after_refresh(self, monkeypatch):
        """refresh_all 后条目包含 title_cn / summary_cn / source_label。"""
        mock_news = [
            {"title": "N", "link": "a", "summary": "S", "source": "openai.com", "published": "2026-05-01"},
        ]
        captured_news = []

        def capture_save(news, path=None):
            captured_news.extend(news)

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", lambda: mock_news)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", capture_save)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        refresh_all()

        assert len(captured_news) == 1
        e = captured_news[0]
        assert e["title_cn"] == "N"
        assert e["summary_cn"] == "S"
        assert e["source_label"] == "OpenAI 官方"

    # --- 部分失败 ---

    def test_one_fetcher_fails_others_succeed(self, monkeypatch):
        """一个板块失败不影响其他板块。"""
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_ai_news",
            lambda: (_ for _ in ()).throw(RuntimeError("News down")),
        )
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        result = refresh_all()

        assert result["ai_news"] == -1
        assert result["skill_projects"] == 0
        assert result["design_projects"] == 0

    def test_two_fetchers_fail_one_succeeds(self, monkeypatch):
        """两个板块失败，一个成功。"""
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_ai_news",
            lambda: (_ for _ in ()).throw(RuntimeError("News down")),
        )
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_skill_projects",
            lambda: (_ for _ in ()).throw(RuntimeError("Skill down")),
        )
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_design_projects",
            lambda: [{"name": "c/d", "description": ""}],
        )
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        result = refresh_all()

        assert result["ai_news"] == -1
        assert result["skill_projects"] == -1
        assert result["design_projects"] == 1

    def test_all_fetchers_fail(self, monkeypatch):
        """全部板块失败时各键值为 -1。"""
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_ai_news",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_skill_projects",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_design_projects",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        result = refresh_all()

        assert result == {"ai_news": -1, "skill_projects": -1, "design_projects": -1}

    def test_save_failure_does_not_crash_pipeline(self, monkeypatch):
        """保存失败（如写权限问题）不应导致 refresh_all 崩溃。"""
        mock_news = [{"title": "N", "link": "a", "summary": "S", "source": "X", "published": "2026-05-01"}]

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", lambda: mock_news)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: [])
        monkeypatch.setattr(
            "src.fetch_pipeline.save_news_to_cache",
            lambda *a, **kw: (_ for _ in ()).throw(IOError("Permission denied")),
        )
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        # save 在 try 块内，失败时整个 ai_news 板块标记 -1
        result = refresh_all()
        assert result["ai_news"] == -1
        assert result["skill_projects"] == 0
        assert result["design_projects"] == 0


# ============================================================================
# 5. get_ai_news / get_skill_projects / get_design_projects — 缓存读取
# ============================================================================

class TestGetFromCache:
    """验证从缓存读取并增强的 get_* 函数。"""

    def test_get_ai_news_from_cache(self, tmp_path, monkeypatch):
        """缓存有数据时返回增强后的条目。"""
        cache_file = tmp_path / "ai_news.json"
        data = [{"title": "T", "link": "L", "summary": "S", "source": "openai.com", "published": "2026-05-01"}]
        cache_file.write_text(json.dumps(data))

        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        result = get_ai_news()
        assert len(result) == 1
        assert result[0]["title_cn"] == "T"
        assert result[0]["source_label"] == "OpenAI 官方"

    def test_get_ai_news_empty_cache(self, tmp_path, monkeypatch):
        """缓存为空时返回空列表。"""
        cache_file = tmp_path / "nonexistent_news.json"
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        result = get_ai_news()
        assert result == []

    def test_get_skill_projects_from_cache(self, tmp_path, monkeypatch):
        """缓存有数据时返回增强后的条目。"""
        cache_file = tmp_path / "skill_projects.json"
        data = [{"name": "x/y", "description": "A test repo"}]
        cache_file.write_text(json.dumps(data))

        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        result = get_skill_projects()
        assert len(result) == 1
        assert result[0]["title_cn"] == "x/y"
        assert result[0]["source_label"] == "GitHub"
        assert "skill_interpretation" in result[0]

    def test_get_skill_projects_empty_cache(self, tmp_path, monkeypatch):
        """缓存为空时返回空列表。"""
        cache_file = tmp_path / "nonexistent_skills.json"
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        result = get_skill_projects()
        assert result == []

    def test_get_design_projects_from_cache(self, tmp_path, monkeypatch):
        """缓存有数据时返回增强后的条目。"""
        cache_file = tmp_path / "design_projects.json"
        data = [{"name": "a/b", "description": "Design tool"}]
        cache_file.write_text(json.dumps(data))

        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        result = get_design_projects()
        assert len(result) == 1
        assert result[0]["source_label"] == "GitHub"
        assert "design_interpretation" in result[0]

    def test_get_design_projects_empty_cache(self, tmp_path, monkeypatch):
        """缓存为空时返回空列表。"""
        cache_file = tmp_path / "nonexistent_designs.json"
        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        result = get_design_projects()
        assert result == []


# ============================================================================
# 6. 边界情况
# ============================================================================

class TestEdgeCases:
    """fetch_pipeline 的边界和防御性处理。"""

    def test_enrich_news_with_none_source__fixed(self):
        """source=None 时 get_source_label 返回 '未知来源'（已修复）。"""
        entries = [{"title": "T", "summary": "S", "source": None}]
        result = _enrich_news(entries)
        assert result[0]["source_label"] == "未知来源"

    def test_enhance_and_translate_projects_kind_variants(self):
        """不同 kind 参数生成不同的 interpretation key。"""
        p_skill = [{"name": "a/b"}]
        p_design = [{"name": "c/d"}]

        r_skill = _enhance_and_translate_projects(p_skill, "skill")
        r_design = _enhance_and_translate_projects(p_design, "design")

        assert "skill_interpretation" in r_skill[0]
        assert "design_interpretation" in r_design[0]
        assert "design_interpretation" not in r_skill[0]
        assert "skill_interpretation" not in r_design[0]

    def test_enhance_and_translate_projects_unknown_kind(self):
        """未知 kind 也不应崩溃，生成 {kind}_interpretation。"""
        projects = [{"name": "x/y"}]
        result = _enhance_and_translate_projects(projects, "other")
        assert "other_interpretation" in result[0]
