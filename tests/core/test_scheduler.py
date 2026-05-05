"""
测试 — scheduler: APScheduler 后台定时调度器。

覆盖范围:
  1. init_scheduler — 调度器启动 / 重复初始化防护
  2. shutdown_scheduler — 调度器关闭 / 幂等
  3. _refresh_all — 各板块独立容错（一个失败不影响其他）
"""

from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from src.scheduler import (
    init_scheduler,
    shutdown_scheduler,
    _refresh_all,
)


class TestInitScheduler:
    """验证 init_scheduler 的行为。"""

    def test_init_starts_scheduler(self, monkeypatch):
        """首次 init 启动 BackgroundScheduler。"""
        mock_scheduler = MagicMock()
        mock_scheduler_class = MagicMock(return_value=mock_scheduler)

        monkeypatch.setattr(
            "src.scheduler.BackgroundScheduler",
            mock_scheduler_class,
        )
        # 重置模块级单例
        monkeypatch.setattr("src.scheduler._scheduler", None)

        app = MagicMock()
        init_scheduler(app)

        mock_scheduler_class.assert_called_once_with(daemon=True)
        mock_scheduler.add_job.assert_called_once()
        mock_scheduler.start.assert_called_once()

    def test_init_adds_cron_job_at_6am(self, monkeypatch):
        """注册每日 6:00 的 cron 任务。"""
        mock_scheduler = MagicMock()
        monkeypatch.setattr(
            "src.scheduler.BackgroundScheduler",
            MagicMock(return_value=mock_scheduler),
        )
        monkeypatch.setattr("src.scheduler._scheduler", None)

        app = MagicMock()
        init_scheduler(app)

        call_kwargs = mock_scheduler.add_job.call_args[1]
        assert call_kwargs["trigger"] == "cron"
        assert call_kwargs["hour"] == 6
        assert call_kwargs["minute"] == 0
        assert call_kwargs["id"] == "daily_refresh"

    def test_double_init_is_safe(self, monkeypatch):
        """重复 init 不创建第二个调度器实例。"""
        mock_scheduler = MagicMock()
        mock_scheduler_class = MagicMock(return_value=mock_scheduler)

        monkeypatch.setattr(
            "src.scheduler.BackgroundScheduler",
            mock_scheduler_class,
        )
        monkeypatch.setattr("src.scheduler._scheduler", None)

        app = MagicMock()
        init_scheduler(app)
        init_scheduler(app)  # 第二次调用

        # BackgroundScheduler 只构造一次
        assert mock_scheduler_class.call_count == 1


class TestShutdownScheduler:
    """验证 shutdown_scheduler 的行为。"""

    def test_shutdown_stops_scheduler(self, monkeypatch):
        """shutdown 调用 scheduler.shutdown()。"""
        mock_scheduler = MagicMock()
        monkeypatch.setattr("src.scheduler._scheduler", mock_scheduler)

        shutdown_scheduler()

        mock_scheduler.shutdown.assert_called_once_with(wait=False)

    def test_shutdown_when_none_is_safe(self, monkeypatch):
        """调度器未初始化时 shutdown 不崩溃。"""
        monkeypatch.setattr("src.scheduler._scheduler", None)
        # 不应抛异常
        shutdown_scheduler()

    def test_shutdown_clears_global(self, monkeypatch):
        """shutdown 后 _scheduler 设为 None。"""
        mock_scheduler = MagicMock()
        monkeypatch.setattr("src.scheduler._scheduler", mock_scheduler)

        shutdown_scheduler()

        # 通过重新获取模块级变量验证
        import src.scheduler as sch
        assert sch._scheduler is None


class TestRefreshAll:
    """验证 _refresh_all 各板块的独立容错。

    _refresh_all 内部通过延迟 import 调用源模块函数，
    因此需要 monkeypatch 源模块而非 scheduler 模块。
    """

    def test_all_three_modules_called(self, monkeypatch):
        """正常情况三个板块都被调用。"""
        mock_ai_news = MagicMock(return_value=[{"title": "News"}])
        mock_skill = MagicMock(return_value=[{"name": "Skill"}])
        mock_design = MagicMock(return_value=[{"name": "Design"}])

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", mock_ai_news)
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enrich_news", lambda x: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", mock_skill)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enhance_and_translate_projects", lambda x, t: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", mock_design)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", MagicMock())

        _refresh_all()

        mock_ai_news.assert_called_once()
        mock_skill.assert_called_once()
        mock_design.assert_called_once()

    def test_ai_news_failure_does_not_block_others(self, monkeypatch):
        """AI 新闻抓取失败不影响其他板块。"""
        mock_skill = MagicMock(return_value=[{"name": "Skill"}])
        mock_design = MagicMock(return_value=[{"name": "Design"}])

        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_ai_news",
            MagicMock(side_effect=RuntimeError("API down")),
        )
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enrich_news", lambda x: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", mock_skill)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enhance_and_translate_projects", lambda x, t: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", mock_design)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", MagicMock())

        _refresh_all()

        mock_skill.assert_called_once()
        mock_design.assert_called_once()

    def test_skill_failure_does_not_block_others(self, monkeypatch):
        """Skill 项目抓取失败不影响其他板块。"""
        mock_ai_news = MagicMock(return_value=[{"title": "News"}])
        mock_design = MagicMock(return_value=[{"name": "Design"}])

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", mock_ai_news)
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enrich_news", lambda x: x)
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_skill_projects",
            MagicMock(side_effect=RuntimeError("API down")),
        )
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enhance_and_translate_projects", lambda x, t: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", mock_design)
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", MagicMock())

        _refresh_all()

        mock_ai_news.assert_called_once()
        mock_design.assert_called_once()

    def test_design_failure_does_not_block_others(self, monkeypatch):
        """Design 项目抓取失败不影响其他板块。"""
        mock_ai_news = MagicMock(return_value=[{"title": "News"}])
        mock_skill = MagicMock(return_value=[{"name": "Skill"}])

        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", mock_ai_news)
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enrich_news", lambda x: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", mock_skill)
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enhance_and_translate_projects", lambda x, t: x)
        monkeypatch.setattr(
            "src.fetch_pipeline.fetch_design_projects",
            MagicMock(side_effect=RuntimeError("API down")),
        )
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", MagicMock())

        _refresh_all()

        mock_ai_news.assert_called_once()
        mock_skill.assert_called_once()

    def test_empty_result_not_saved(self, monkeypatch):
        """空结果不触发保存。"""
        mock_save_news = MagicMock()
        monkeypatch.setattr("src.fetch_pipeline.fetch_ai_news", MagicMock(return_value=[]))
        monkeypatch.setattr("src.fetch_pipeline.save_news_to_cache", mock_save_news)
        monkeypatch.setattr("src.fetch_pipeline._enrich_news", lambda x: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_skill_projects", MagicMock(return_value=[{"name": "X"}]))
        monkeypatch.setattr("src.fetch_pipeline.save_skill_projects", MagicMock())
        monkeypatch.setattr("src.fetch_pipeline._enhance_and_translate_projects", lambda x, t: x)
        monkeypatch.setattr("src.fetch_pipeline.fetch_design_projects", MagicMock(return_value=[{"name": "Y"}]))
        monkeypatch.setattr("src.fetch_pipeline.save_design_projects", MagicMock())

        _refresh_all()

        # 空结果不保存
        mock_save_news.assert_not_called()
