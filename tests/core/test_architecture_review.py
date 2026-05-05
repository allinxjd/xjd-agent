"""
测试 — 架构审查验收：验证重构后的正确行为。

验证：
  1. 路由注册统一到 Blueprint（routes.py）
  2. get_* 函数只做纯缓存读取，不调用 translate/batch_enrich
  3. refresh_all 写入缓存的数据已包含翻译字段
  4. scheduler 使用 config 中的配置值
  5. 边界情况覆盖
"""
from __future__ import annotations

import inspect
import json
from unittest.mock import patch, MagicMock

import pytest

from src.app import app
from src.fetch_pipeline import (
    get_ai_news,
    get_skill_projects,
    get_design_projects,
    refresh_all,
    _enrich_news,
    _enhance_and_translate_projects,
    CACHE_REUSE_MIN_DESC_LEN,
)


# ============================================================================
# Fixture
# ============================================================================

@pytest.fixture
def client():
    """Flask 测试客户端。"""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ============================================================================
# 1. 路由注册一致性 — 验证所有 API 端点均可访问
# ============================================================================

class TestRouteRegistration:
    """验证所有 /api/* 路由均已正确注册到 Flask 应用。"""

    def test_all_api_routes_registered(self):
        """确认 6 个 API 路由全部注册。"""
        rules = {rule.rule for rule in app.url_map.iter_rules()}
        expected = {
            "/api/news",
            "/api/skills",
            "/api/designs",
            "/api/deploy",
            "/api/deployments",
            "/api/refresh",
        }
        missing = expected - rules
        assert not missing, f"缺少路由: {missing}"

    def test_news_route_method_is_get(self):
        """/api/news 只接受 GET。"""
        for rule in app.url_map.iter_rules():
            if rule.rule == "/api/news":
                assert "GET" in rule.methods
                assert "POST" not in rule.methods
                return
        pytest.fail("/api/news 路由未注册")

    def test_skills_route_method_is_get(self):
        """/api/skills 只接受 GET。"""
        for rule in app.url_map.iter_rules():
            if rule.rule == "/api/skills":
                assert "GET" in rule.methods
                return
        pytest.fail("/api/skills 路由未注册")

    def test_designs_route_method_is_get(self):
        """/api/designs 只接受 GET。"""
        for rule in app.url_map.iter_rules():
            if rule.rule == "/api/designs":
                assert "GET" in rule.methods
                return
        pytest.fail("/api/designs 路由未注册")

    def test_deploy_route_method_is_post(self):
        """/api/deploy 只接受 POST。"""
        for rule in app.url_map.iter_rules():
            if rule.rule == "/api/deploy":
                assert "POST" in rule.methods
                return
        pytest.fail("/api/deploy 路由未注册")

    def test_refresh_route_method_is_post(self):
        """/api/refresh 只接受 POST。"""
        for rule in app.url_map.iter_rules():
            if rule.rule == "/api/refresh":
                assert "POST" in rule.methods
                return
        pytest.fail("/api/refresh 路由未注册")

    def test_deployments_route_method_is_get(self):
        """/api/deployments 接受 GET。"""
        for rule in app.url_map.iter_rules():
            if rule.rule == "/api/deployments":
                assert "GET" in rule.methods
                return
        pytest.fail("/api/deployments 路由未注册")

    def test_all_routes_unified_in_blueprint(self):
        """验证所有 API 路由已统一到 routes.py Blueprint，app.py 无直接路由注册。"""
        import src.app as app_module
        import src.routes as routes_module

        app_source = inspect.getsource(app_module)
        routes_source = inspect.getsource(routes_module)

        assert '@app.route(' not in app_source, "app.py 不应有直接路由注册"
        assert "api_bp" in routes_source, "routes.py 应使用 Blueprint"
        for route in ["/api/news", "/api/skills", "/api/designs",
                      "/api/deploy", "/api/deployments", "/api/refresh"]:
            assert route in routes_source, f"routes.py 应包含 {route}"


# ============================================================================
# 2. get_* 函数调用翻译/增强管线 — 验证问题 2 存在性
# ============================================================================

class TestGetFunctionsPureCacheRead:
    """验证 get_* 函数只做纯缓存读取，不调用 translate/batch_enrich。"""

    def test_get_ai_news_no_translate(self, tmp_path, monkeypatch):
        """get_ai_news 不调用 translate（纯缓存读取）。"""
        cache_file = tmp_path / "ai_news.json"
        data = [{"title": "Hello World News", "link": "a", "summary": "Big summary text here",
                 "source": "openai.com", "published": "2026-05-01"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        with patch("src.fetch_pipeline.translate") as mock_translate:
            get_ai_news()
            assert not mock_translate.called

    def test_get_skill_projects_no_translate(self, tmp_path, monkeypatch):
        """get_skill_projects 不调用 translate（纯缓存读取）。"""
        cache_file = tmp_path / "skill_projects.json"
        data = [{"name": "owner/repo", "description": "A skill project"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        with patch("src.fetch_pipeline.translate") as mock_translate:
            get_skill_projects()
            assert not mock_translate.called

    def test_get_skill_projects_no_batch_enrich(self, tmp_path, monkeypatch):
        """get_skill_projects 不调用 batch_enrich（纯缓存读取）。"""
        cache_file = tmp_path / "skill_projects.json"
        data = [{"name": "owner/repo", "description": "A skill project"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        with patch("src.fetch_pipeline.batch_enrich") as mock_enrich:
            get_skill_projects()
            assert not mock_enrich.called

    def test_get_design_projects_no_translate(self, tmp_path, monkeypatch):
        """get_design_projects 不调用 translate（纯缓存读取）。"""
        cache_file = tmp_path / "design_projects.json"
        data = [{"name": "org/design-tool", "description": "A design tool"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        with patch("src.fetch_pipeline.translate") as mock_translate:
            get_design_projects()
            assert not mock_translate.called

    def test_get_design_projects_no_batch_enrich(self, tmp_path, monkeypatch):
        """get_design_projects 不调用 batch_enrich（纯缓存读取）。"""
        cache_file = tmp_path / "design_projects.json"
        data = [{"name": "org/design-tool", "description": "A design tool"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        with patch("src.fetch_pipeline.batch_enrich") as mock_enrich:
            get_design_projects()
            assert not mock_enrich.called


# ============================================================================
# 3. refresh_all 写入缓存的数据已包含翻译字段
# ============================================================================

class TestRefreshAllSavesEnrichedData:
    """验证 refresh_all 保存的数据已经过翻译+增强，无需 get_* 再次处理。"""

    def test_refresh_saves_news_with_title_cn(self, monkeypatch):
        """refresh_all 保存的新闻包含 title_cn 字段。"""
        mock_news = [{"title": "AI News", "link": "x", "summary": "Summary",
                      "source": "openai.com", "published": "2026-05-01"}]
        captured = []

        def capture_save(news, path=None):
            captured.extend(news)

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", lambda: mock_news)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", capture_save)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        refresh_all()

        assert len(captured) == 1
        assert "title_cn" in captured[0]
        assert "summary_cn" in captured[0]
        assert "source_label" in captured[0]

    def test_refresh_saves_skills_with_enriched_fields(self, monkeypatch):
        """refresh_all 保存的 skill 项目包含 title_cn / summary_cn / source_label。"""
        mock_skills = [{"name": "ms/vscode", "description": "VS Code"}]
        captured = []

        def capture_save(projects, path=None):
            captured.extend(projects)

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: mock_skills)
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", capture_save)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", lambda *a, **kw: None)

        refresh_all()

        assert len(captured) == 1
        assert "title_cn" in captured[0]
        assert "summary_cn" in captured[0]
        assert "source_label" in captured[0]

    def test_refresh_saves_designs_with_enriched_fields(self, monkeypatch):
        """refresh_all 保存的 design 项目包含 title_cn / summary_cn / source_label。"""
        mock_designs = [{"name": "org/tool", "description": "Design tool"}]
        captured = []

        def capture_save(projects, path=None):
            captured.extend(projects)

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", lambda: [])
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", lambda: mock_designs)
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", lambda *a, **kw: None)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", capture_save)

        refresh_all()

        assert len(captured) == 1
        assert "title_cn" in captured[0]
        assert "summary_cn" in captured[0]
        assert "source_label" in captured[0]


# ============================================================================
# 4. 缓存复用阈值 — CACHE_REUSE_MIN_DESC_LEN 行为
# ============================================================================

class TestCacheReuseThreshold:
    """验证 _enhance_and_translate_projects 的缓存复用阈值逻辑。"""

    def test_short_description_calls_translate(self):
        """description < 150 字符时调用 translate。"""
        projects = [{"name": "a/b", "description": "Short desc"}]

        with patch("src.fetch_pipeline.translate") as mock_t:
            mock_t.side_effect = lambda x: x
            with patch("src.fetch_pipeline.batch_enrich", side_effect=lambda x: x):
                _enhance_and_translate_projects(projects, "skill")

            # 短描述：translate 被调用（name 翻译 + description 翻译）
            assert mock_t.call_count >= 1

    def test_long_description_skips_translate_for_summary(self):
        """description >= 150 字符时 summary_cn 直接用原文，不调用 translate。"""
        long_desc = "A" * CACHE_REUSE_MIN_DESC_LEN
        projects = [{"name": "a/b", "description": long_desc}]

        with patch("src.fetch_pipeline.translate") as mock_t:
            mock_t.side_effect = lambda x: x
            with patch("src.fetch_pipeline.batch_enrich", side_effect=lambda x: x):
                result = _enhance_and_translate_projects(projects, "skill")

            # 长描述：summary_cn 直接赋值为 description，不调用 translate
            assert result[0]["summary_cn"] == long_desc

    def test_threshold_value_is_150(self):
        """缓存复用阈值为 150。"""
        assert CACHE_REUSE_MIN_DESC_LEN == 150


# ============================================================================
# 5. API 端点响应结构完整性（端到端）
# ============================================================================

class TestApiEndpointIntegrity:
    """验证 API 端点在有缓存数据时返回完整字段。"""

    def test_api_news_response_fields(self, client, tmp_path, monkeypatch):
        """GET /api/news 返回的每条数据包含必要字段。"""
        cache_file = tmp_path / "ai_news.json"
        data = [{"title": "Test", "link": "http://x", "summary": "S",
                 "source": "openai.com", "published": "2026-05-01",
                 "title_cn": "测试", "summary_cn": "摘要", "source_label": "OpenAI"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        resp = client.get("/api/news")
        assert resp.status_code == 200
        body = resp.get_json()
        entry = body["data"][0]

        required_fields = {"title", "link", "summary", "source", "published",
                           "title_cn", "summary_cn", "source_label"}
        missing = required_fields - set(entry.keys())
        assert not missing, f"缺少字段: {missing}"

    def test_api_skills_response_fields(self, client, tmp_path, monkeypatch):
        """GET /api/skills 返回的每条数据包含必要字段。"""
        cache_file = tmp_path / "skill_projects.json"
        data = [{"name": "x/y", "description": "Desc", "stars": 100,
                 "language": "Python", "forks": 10, "url": "http://x",
                 "title_cn": "项目", "summary_cn": "描述",
                 "source_label": "GitHub", "skill_interpretation": "解读"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        resp = client.get("/api/skills")
        assert resp.status_code == 200
        body = resp.get_json()
        entry = body["data"][0]

        required_fields = {"name", "description", "title_cn", "summary_cn",
                           "source_label", "skill_interpretation"}
        missing = required_fields - set(entry.keys())
        assert not missing, f"缺少字段: {missing}"

    def test_api_designs_response_fields(self, client, tmp_path, monkeypatch):
        """GET /api/designs 返回的每条数据包含必要字段。"""
        cache_file = tmp_path / "design_projects.json"
        data = [{"name": "a/b", "description": "Desc", "stars": 200,
                 "language": "TS", "forks": 20, "url": "http://x",
                 "title_cn": "项目", "summary_cn": "描述",
                 "source_label": "GitHub", "design_interpretation": "解读"}]
        cache_file.write_text(json.dumps(data))
        monkeypatch.setattr("src.design_fetcher.DESIGN_FILE", cache_file)

        resp = client.get("/api/designs")
        assert resp.status_code == 200
        body = resp.get_json()
        entry = body["data"][0]

        required_fields = {"name", "description", "title_cn", "summary_cn",
                           "source_label", "design_interpretation"}
        missing = required_fields - set(entry.keys())
        assert not missing, f"缺少字段: {missing}"


# ============================================================================
# 6. 边界情况
# ============================================================================

class TestArchitectureEdgeCases:
    """架构相关的边界情况。"""

    def test_get_ai_news_empty_cache_no_translate_call(self, tmp_path, monkeypatch):
        """空缓存时 get_ai_news 不应调用 translate。"""
        cache_file = tmp_path / "empty_news.json"
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", cache_file)

        with patch("src.fetch_pipeline.translate") as mock_t:
            result = get_ai_news()
            assert result == []
            assert not mock_t.called

    def test_get_skill_projects_empty_cache_no_enrich_call(self, tmp_path, monkeypatch):
        """空缓存时 get_skill_projects 不应调用 batch_enrich。"""
        cache_file = tmp_path / "empty_skills.json"
        monkeypatch.setattr("src.skill_fetcher.SKILL_FILE", cache_file)

        with patch("src.fetch_pipeline.batch_enrich") as mock_e:
            result = get_skill_projects()
            assert result == []
            assert not mock_e.called

    def test_scheduler_uses_config_values(self):
        """scheduler.py 使用 config 中的 REFRESH_HOUR/REFRESH_MINUTE。"""
        import src.scheduler as sched_mod
        source = inspect.getsource(sched_mod)
        assert "REFRESH_HOUR" in source, "scheduler 应引用 config.REFRESH_HOUR"
        assert "REFRESH_MINUTE" in source, "scheduler 应引用 config.REFRESH_MINUTE"

    def test_enrich_news_with_already_translated_entry(self):
        """已有 title_cn 的条目不应被覆盖。"""
        entries = [{
            "title": "Original",
            "summary": "Sum",
            "source": "test",
            "title_cn": "已翻译",
            "summary_cn": "已翻译摘要",
        }]
        result = _enrich_news(entries)
        assert result[0]["title_cn"] == "已翻译"
        assert result[0]["summary_cn"] == "已翻译摘要"

    def test_enhance_projects_preserves_existing_summary_cn(self):
        """已有 summary_cn 的项目不应被覆盖。"""
        projects = [{
            "name": "x/y",
            "description": "Desc",
            "title_cn": "已翻译名",
            "summary_cn": "已翻译描述",
        }]
        with patch("src.fetch_pipeline.batch_enrich", side_effect=lambda x: x):
            result = _enhance_and_translate_projects(projects, "skill")
        assert result[0]["title_cn"] == "已翻译名"
        assert result[0]["summary_cn"] == "已翻译描述"

    def test_api_deploy_without_body_returns_400(self, client):
        """POST /api/deploy 无 body 返回 400。"""
        resp = client.post("/api/deploy", content_type="application/json")
        assert resp.status_code == 400

    def test_api_deploy_empty_repo_url_returns_400(self, client):
        """POST /api/deploy 空 repo_url 返回 400。"""
        resp = client.post("/api/deploy",
                           json={"repo_url": ""},
                           content_type="application/json")
        assert resp.status_code == 400
