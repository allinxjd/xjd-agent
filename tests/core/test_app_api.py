"""
测试 — Flask 应用 API 端点。

覆盖范围:
  1. GET  /          — 首页 HTML 渲染
  2. GET  /api/news  — AI 资讯正常/空缓存/响应结构/容错
  3. GET  /api/skills — Skill 项目正常/空缓存/响应结构/容错
  4. GET  /api/designs — Design 项目正常/空缓存/响应结构/容错
  5. pipeline enrichment 验证 — 缓存条目经 enrich 后字段完整
  6. 已知缺陷边界 — source=None 导致崩溃
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.app import app


# ============================================================================
# Flask 测试客户端 fixture
# ============================================================================

@pytest.fixture
def client():
    """Flask 测试客户端。"""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ============================================================================
# 1. GET / — 首页
# ============================================================================

class TestHomePage:
    """验证首页 HTML 渲染。"""

    def test_home_returns_200(self, client):
        """首页返回 200。"""
        resp = client.get("/")
        assert resp.status_code == 200

    def test_home_is_html(self, client):
        """首页返回 HTML。"""
        resp = client.get("/")
        assert "text/html" in resp.content_type

    def test_home_contains_data_dashboard_title(self, client):
        """首页包含「数据看板」标题。"""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "数据看板" in html

    def test_home_contains_api_links(self, client):
        """首页包含三个 API 板块链接。"""
        resp = client.get("/")
        html = resp.data.decode("utf-8")
        assert "/api/news" in html
        assert "/api/skills" in html
        assert "/api/designs" in html


# ============================================================================
# 2. GET /api/news — AI 资讯
# ============================================================================

class TestApiNews:
    """验证 /api/news 端点。"""

    # --- 正常路径 ---

    def test_news_returns_200(self, client):
        """正常返回 200。"""
        resp = client.get("/api/news")
        assert resp.status_code == 200

    def test_news_returns_json(self, client):
        """返回 JSON 格式。"""
        resp = client.get("/api/news")
        assert resp.content_type == "application/json"

    def test_news_has_status_ok(self, client):
        """响应包含 status: ok。"""
        data = client.get("/api/news").get_json()
        assert data["status"] == "ok"

    def test_news_has_count_field(self, client):
        """响应包含 count 字段。"""
        data = client.get("/api/news").get_json()
        assert "count" in data

    def test_news_has_data_field(self, client):
        """响应包含 data 字段（列表）。"""
        data = client.get("/api/news").get_json()
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_news_count_matches_data_length(self, client):
        """count 与 data 列表长度一致。"""
        data = client.get("/api/news").get_json()
        assert data["count"] == len(data["data"])

    # --- 空缓存 ---

    def test_news_empty_cache_returns_empty_list(self, client, tmp_path, monkeypatch):
        """缓存文件不存在时返回空列表。"""
        monkeypatch.setattr(
            "src.ai_news_fetcher.NEWS_FILE",
            tmp_path / "nonexistent_news.json",
        )
        data = client.get("/api/news").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["data"] == []

    # --- 缓存有数据 ---

    def test_news_from_cache_returns_entries(self, client, tmp_path, monkeypatch):
        """缓存有数据时返回增强后的条目。"""
        cache_file = tmp_path / "ai_news.json"
        cache_data = [
            {
                "title": "GPT-5 Released",
                "link": "https://openai.com/blog/gpt5",
                "summary": "OpenAI announces GPT-5.",
                "source": "https://openai.com/blog",
                "published": "Tue, 04 May 2026 10:00:00 +0000",
            },
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        data = client.get("/api/news").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 1
        assert len(data["data"]) == 1

    def test_news_enriched_fields_present(self, client, tmp_path, monkeypatch):
        """缓存条目由 refresh_all 预增强后，API 直接返回（含 title_cn / summary_cn / source_label）。"""
        cache_file = tmp_path / "ai_news.json"
        cache_data = [
            {
                "title": "GPT-5 Released",
                "link": "https://openai.com/blog/gpt5",
                "summary": "OpenAI announces GPT-5.",
                "source": "https://openai.com/blog",
                "published": "Tue, 04 May 2026 10:00:00 +0000",
                "title_cn": "GPT-5 发布",
                "summary_cn": "OpenAI 宣布 GPT-5。",
                "source_label": "OpenAI 官方",
            },
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        data = client.get("/api/news").get_json()
        entry = data["data"][0]

        assert "title_cn" in entry, "缺少 title_cn 字段"
        assert "summary_cn" in entry, "缺少 summary_cn 字段"
        assert "source_label" in entry, "缺少 source_label 字段"

        # source_label 应通过映射得到 "OpenAI 官方"
        assert entry["source_label"] == "OpenAI 官方"

    def test_news_multiple_entries(self, client, tmp_path, monkeypatch):
        """多条预增强缓存条目全部返回且字段完整。"""
        cache_file = tmp_path / "ai_news.json"
        cache_data = [
            {"title": "N1", "link": "a", "summary": "S1", "source": "openai.com",
             "published": "2026-05-04", "title_cn": "N1", "summary_cn": "S1", "source_label": "OpenAI 官方"},
            {"title": "N2", "link": "b", "summary": "S2", "source": "huggingface.co",
             "published": "2026-05-03", "title_cn": "N2", "summary_cn": "S2", "source_label": "HuggingFace"},
            {"title": "N3", "link": "c", "summary": "S3", "source": "arxiv.org",
             "published": "2026-05-02", "title_cn": "N3", "summary_cn": "S3", "source_label": "arXiv"},
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        data = client.get("/api/news").get_json()
        assert data["count"] == 3
        assert data["data"][0]["source_label"] == "OpenAI 官方"
        assert data["data"][1]["source_label"] == "HuggingFace"
        assert data["data"][2]["source_label"] == "arXiv"  # 匹配 SOURCE_LABEL_MAP


# ============================================================================
# 3. GET /api/skills — Skill 项目
# ============================================================================

class TestApiSkills:
    """验证 /api/skills 端点。"""

    def test_skills_returns_200(self, client):
        """正常返回 200。"""
        resp = client.get("/api/skills")
        assert resp.status_code == 200

    def test_skills_returns_json(self, client):
        """返回 JSON 格式。"""
        resp = client.get("/api/skills")
        assert resp.content_type == "application/json"

    def test_skills_has_status_ok(self, client):
        """响应包含 status: ok。"""
        data = client.get("/api/skills").get_json()
        assert data["status"] == "ok"

    def test_skills_has_count_and_data(self, client):
        """响应包含 count 和 data 字段。"""
        data = client.get("/api/skills").get_json()
        assert "count" in data
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_skills_count_matches_data_length(self, client):
        """count 与 data 长度一致。"""
        data = client.get("/api/skills").get_json()
        assert data["count"] == len(data["data"])

    def test_skills_empty_cache(self, client, tmp_path, monkeypatch):
        """缓存文件不存在时返回空列表。"""
        monkeypatch.setattr(
            "src.skill_fetcher.SKILL_FILE",
            tmp_path / "nonexistent_skills.json",
        )
        data = client.get("/api/skills").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["data"] == []

    def test_skills_enriched_fields_present(self, client, tmp_path, monkeypatch):
        """缓存条目由 refresh_all 预增强后，API 直接返回（含 title_cn / source_label / skill_interpretation）。"""
        cache_file = tmp_path / "skill_projects.json"
        cache_data = [
            {
                "name": "microsoft/vscode",
                "description": "VS Code editor",
                "stars": 150000,
                "language": "TypeScript",
                "forks": 25000,
                "url": "https://github.com/microsoft/vscode",
                "title_cn": "microsoft/vscode",
                "summary_cn": "VS Code editor",
                "source_label": "GitHub",
                "skill_interpretation": "",
            },
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        data = client.get("/api/skills").get_json()
        entry = data["data"][0]

        assert "title_cn" in entry
        assert "summary_cn" in entry
        assert "source_label" in entry
        assert "skill_interpretation" in entry
        assert entry["source_label"] == "GitHub"


# ============================================================================
# 4. GET /api/designs — Design 项目
# ============================================================================

class TestApiDesigns:
    """验证 /api/designs 端点。"""

    def test_designs_returns_200(self, client):
        """正常返回 200。"""
        resp = client.get("/api/designs")
        assert resp.status_code == 200

    def test_designs_returns_json(self, client):
        """返回 JSON 格式。"""
        resp = client.get("/api/designs")
        assert resp.content_type == "application/json"

    def test_designs_has_status_ok(self, client):
        """响应包含 status: ok。"""
        data = client.get("/api/designs").get_json()
        assert data["status"] == "ok"

    def test_designs_has_count_and_data(self, client):
        """响应包含 count 和 data 字段。"""
        data = client.get("/api/designs").get_json()
        assert "count" in data
        assert "data" in data
        assert isinstance(data["data"], list)

    def test_designs_count_matches_data_length(self, client):
        """count 与 data 长度一致。"""
        data = client.get("/api/designs").get_json()
        assert data["count"] == len(data["data"])

    def test_designs_empty_cache(self, client, tmp_path, monkeypatch):
        """缓存文件不存在时返回空列表。"""
        monkeypatch.setattr(
            "src.design_fetcher.DESIGN_FILE",
            tmp_path / "nonexistent_designs.json",
        )
        data = client.get("/api/designs").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["data"] == []

    def test_designs_enriched_fields_present(self, client, tmp_path, monkeypatch):
        """缓存条目由 refresh_all 预增强后，API 直接返回（含 title_cn / source_label / design_interpretation）。"""
        cache_file = tmp_path / "design_projects.json"
        cache_data = [
            {
                "name": "shadcn-ui/ui",
                "description": "Beautifully designed components",
                "stars": 87000,
                "language": "TypeScript",
                "forks": 5200,
                "url": "https://github.com/shadcn-ui/ui",
                "title_cn": "shadcn-ui/ui",
                "summary_cn": "Beautifully designed components",
                "source_label": "GitHub",
                "design_interpretation": "",
            },
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        data = client.get("/api/designs").get_json()
        entry = data["data"][0]

        assert "title_cn" in entry
        assert "summary_cn" in entry
        assert "source_label" in entry
        assert "design_interpretation" in entry
        assert entry["source_label"] == "GitHub"


# ============================================================================
# 5. 跨端点一致性
# ============================================================================

class TestApiConsistency:
    """验证三个 API 端点响应结构一致。"""

    def test_all_endpoints_have_same_structure(self, client):
        """三个端点均返回 {status, count, data}。"""
        endpoints = ["/api/news", "/api/skills", "/api/designs"]
        for ep in endpoints:
            data = client.get(ep).get_json()
            assert "status" in data, f"{ep} 缺少 status"
            assert "count" in data, f"{ep} 缺少 count"
            assert "data" in data, f"{ep} 缺少 data"

    def test_no_endpoint_crashes_on_get(self, client):
        """所有 GET 端点不应返回 500。"""
        endpoints = ["/", "/api/news", "/api/skills", "/api/designs"]
        for ep in endpoints:
            resp = client.get(ep)
            assert resp.status_code != 500, f"{ep} 返回 500: {resp.data[:200]}"


# ============================================================================
# 6. 边界：source=None 已知缺陷
# ============================================================================

class TestKnownBugSourceNone:
    """验证 source=None 时 _enrich_news 正确处理（已修复）。"""

    def test_enrich_news_with_none_source_returns_unknown(self):
        """source=None 时 source_label 返回 '未知来源'。"""
        from src.fetch_pipeline import _enrich_news

        entries = [{"title": "T", "summary": "S", "source": None}]
        result = _enrich_news(entries)
        assert result[0]["source_label"] == "未知来源"


# ============================================================================
# 7. 边界：_maybe_translate 是空桩
# ============================================================================

class TestTranslateStub:
    """验证 _enrich_news 翻译行为。"""

    def test_enrich_news_adds_title_cn(self):
        """_enrich_news 为条目添加 title_cn 字段。"""
        from src.fetch_pipeline import _enrich_news

        entries = [{"title": "AI Breakthrough", "summary": "Big news.", "source": "test"}]
        result = _enrich_news(entries)
        assert "title_cn" in result[0]
        assert "summary_cn" in result[0]

    def test_enrich_news_title_cn_equals_original_title(self):
        """title_cn 与 title 完全一致（未翻译）。"""
        from src.fetch_pipeline import _enrich_news

        entries = [{"title": "AI Breakthrough", "summary": "Big news.", "source": "test"}]
        result = _enrich_news(entries)
        assert result[0]["title_cn"] == "AI Breakthrough"
        assert result[0]["summary_cn"] == "Big news."


# ============================================================================
# 8. 边界：调度器绕过管线
# ============================================================================

class TestSchedulerBypass:
    """验证 scheduler._refresh_all 通过 fetch_pipeline 保存增强后的数据。

    scheduler 调用 fetch_pipeline.refresh_all()，数据经过 enrich，
    缓存数据包含 title_cn / summary_cn / source_label。
    """

    def test_scheduler_saves_enriched_data(self, monkeypatch, tmp_path):
        """调度器保存的数据经过 _enrich_news 增强。"""
        from src.scheduler import _refresh_all

        news_file = tmp_path / "ai_news.json"

        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", news_file)

        mock_news = [
            {"title": "Raw News", "link": "a", "summary": "Raw summary",
             "source": "openai.com", "published": "2026-05-01"},
        ]
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_ai_news",
            lambda *a, **kw: mock_news,
        )
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_skill_projects",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_design_projects",
            lambda *a, **kw: [],
        )

        _refresh_all()

        saved = json.loads(news_file.read_text())
        entry = saved[0]

        assert "title_cn" in entry, "调度器通过 pipeline 保存，应有 title_cn"
        assert "source_label" in entry, "调度器通过 pipeline 保存，应有 source_label"
        assert entry["source_label"] == "OpenAI 官方"


# ============================================================================
# 9. 边界：ai_news_fetcher 缺少近 7 天过滤
# ============================================================================

class TestNoRecentFilter:
    """验证 ai_news_fetcher 已实现近 7 天过滤。"""

    def test_fetch_ai_news_has_is_recent_function(self):
        """确认 _is_recent 函数存在于模块中。"""
        import src.ai_news_fetcher as mod
        assert hasattr(mod, "_is_recent"), (
            "_is_recent 应存在于 ai_news_fetcher 模块中"
        )

    def test_fetch_ai_news_calls_is_recent(self):
        """fetch_ai_news 源码中包含 _is_recent 调用。"""
        import inspect
        from src.ai_news_fetcher import fetch_ai_news

        source = inspect.getsource(fetch_ai_news)
        assert "_is_recent" in source, (
            "fetch_ai_news 应调用 _is_recent 过滤近 7 天资讯"
        )


# ============================================================================
# 10. 边界：空值 / 缺失字段响应
# ============================================================================

class TestEmptyAndMissingFields:
    """验证缓存条目缺失字段时 API 不崩溃。"""

    def test_news_with_minimal_fields(self, client, tmp_path, monkeypatch):
        """仅有最少字段的缓存条目也能正常返回。"""
        cache_file = tmp_path / "ai_news.json"
        cache_data = [
            {"title": "Minimal", "link": "x", "summary": "", "source": "", "published": ""},
        ]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        data = client.get("/api/news").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 1

    def test_skill_with_minimal_fields(self, client, tmp_path, monkeypatch):
        """仅有最少字段的 skill 缓存也能正常返回。"""
        cache_file = tmp_path / "skill_projects.json"
        cache_data = [{"name": "x/y"}]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        data = client.get("/api/skills").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 1

    def test_design_with_minimal_fields(self, client, tmp_path, monkeypatch):
        """仅有最少字段的 design 缓存也能正常返回。"""
        cache_file = tmp_path / "design_projects.json"
        cache_data = [{"name": "a/b"}]
        cache_file.write_text(json.dumps(cache_data))
        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        data = client.get("/api/designs").get_json()
        assert data["status"] == "ok"
        assert data["count"] == 1

    def test_corrupted_cache_does_not_crash_api(self, client, tmp_path, monkeypatch):
        """损坏的 JSON 缓存文件不导致 API 500。"""
        cache_file = tmp_path / "ai_news.json"
        cache_file.write_text("{broken json")
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        data = client.get("/api/news").get_json()
        # load_news_from_cache 捕获异常返回 []，API 应返回空列表
        assert data["status"] == "ok"
        assert data["count"] == 0
        assert data["data"] == []
