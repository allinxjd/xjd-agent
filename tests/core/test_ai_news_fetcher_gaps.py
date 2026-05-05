"""
补充测试 — ai_news_fetcher: _parse_published 时区处理 / fetch_ai_news 边界。

覆盖范围:
  1. _parse_published — tzinfo 为 None 的已解析 datetime 处理
  2. _parse_published — datetime.min 跨平台一致性
  3. fetch_ai_news — bozo=True 但有 entry / updated 回退
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from src.ai_news_fetcher import _parse_published, fetch_ai_news


# ============================================================================
# 1. _parse_published — 时区处理与 datetime.min 一致性
# ============================================================================

class TestSortKeyTimezone:
    """验证 _parse_published 的时区处理边界。"""

    def test_parsed_date_without_tzinfo_still_comparable(self):
        """parsedate_to_datetime 可能返回 naive datetime，
        _parse_published 不修改时区，直接返回 naive datetime。"""
        entry = {
            "published": "Tue, 04 May 2026 10:30:00"
        }
        result = _parse_published(entry)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 4

    def test_datetime_min_has_utc_tzinfo(self):
        """datetime.min.replace(tzinfo=timezone.utc) 返回带 UTC 时区的 datetime.min。"""
        expected = datetime.min.replace(tzinfo=timezone.utc)
        result = _parse_published({"published": ""})
        assert result == expected
        assert result.tzinfo == timezone.utc

    def test_datetime_min_always_utc(self):
        """无论何种失败，_parse_published 都应返回 tzinfo=UTC 的 datetime.min。"""
        for bad_input in ["", "garbage", "!@#$%"]:
            entry = {"published": bad_input}
            result = _parse_published(entry)
            assert result.tzinfo == timezone.utc, \
                f"bad_input={bad_input!r} 应返回 UTC datetime.min, 实际 tzinfo={result.tzinfo}"

    def test_missing_key_returns_utc_datetime_min(self):
        """缺失 published 键 → UTC datetime.min。"""
        result = _parse_published({})
        assert result.tzinfo == timezone.utc
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_naive_datetime_comparison_with_utc_datetime_min(self):
        """naive datetime 与 UTC datetime.min 可以比较（Python 3.9+）。"""
        naive = _parse_published({"published": "Tue, 04 May 2026 10:30:00"})
        utc_min = datetime.min.replace(tzinfo=timezone.utc)
        # naive > UTC datetime.min 应成立
        assert naive > utc_min

    def test_utc_aware_datetime_comparison(self):
        """两个 UTC aware datetime 正确比较。"""
        newer = _parse_published({"published": "Tue, 04 May 2026 10:00:00 +0000"})
        older = _parse_published({"published": "Mon, 03 May 2026 10:00:00 +0000"})
        assert newer > older


# ============================================================================
# 2. fetch_ai_news — bozo/updated 边界
# ============================================================================

class TestFetchAiNewsBoundary:
    """验证 fetch_ai_news 的边界行为。"""

    def test_bozo_true_with_entries_still_works(self, monkeypatch):
        """bozo=True 但有 entries → 不跳过，正常返回数据。"""
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        feed = MagicMock()
        feed.bozo = True
        feed.bozo_exception = Exception("minor parse warning")
        feed.feed = {"title": "WarnSource"}

        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "title": "Still valid",
            "link": "https://valid.com",
            "summary": "Content here",
            "published": "Tue, 04 May 2026 10:00:00 +0000",
        }.get(key, default)

        feed.entries = [entry]
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://warn/rss"])
        assert len(result) == 1
        assert result[0]["title"] == "Still valid"

    def test_published_fallback_to_updated(self, monkeypatch):
        """published 缺失时回退到 updated。"""
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        feed = MagicMock()
        feed.bozo = False
        feed.feed = {"title": "TestSource"}

        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "title": "Updated entry",
            "link": "https://test.com",
            "summary": "Content",
            "published": "",
            "updated": "Tue, 04 May 2026 08:00:00 +0000",
        }.get(key, default)

        feed.entries = [entry]
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://test/rss"])
        assert len(result) == 1
        # published 字段应为 "Tue, 04 May 2026 08:00:00 +0000"（来自 updated）
        assert result[0]["published"] == "Tue, 04 May 2026 08:00:00 +0000"

    def test_both_published_and_updated_missing(self, monkeypatch):
        """published 和 updated 都缺失 → published 为空字符串。"""
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        feed = MagicMock()
        feed.bozo = False
        feed.feed = {"title": "TestSource"}

        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "title": "No date entry",
            "link": "https://nodate.com",
            "summary": "Content",
            "published": "",
            "updated": "",
        }.get(key, default)

        feed.entries = [entry]
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://nodate/rss"])
        assert len(result) == 1
        assert result[0]["published"] == ""

    def test_feed_title_missing_falls_back_to_url(self, monkeypatch):
        """feed.feed 缺少 title → 回退到源 URL。"""
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        feed = MagicMock()
        feed.bozo = False
        feed.feed = {}  # 无 title

        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "title": "Entry",
            "link": "https://e.com",
            "summary": "S",
            "published": "Tue, 04 May 2026",
        }.get(key, default)

        feed.entries = [entry]
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://my-source/rss"])
        assert len(result) == 1
        # source 回退到源 URL
        assert result[0]["source"] == "https://my-source/rss"
