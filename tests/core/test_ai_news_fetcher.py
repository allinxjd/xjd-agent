"""
测试 — ai_news_fetcher: 摘要清洗、缓存读写、排序正确性。

覆盖范围:
  1. _clean_summary — HTML 剥离 / 截断 / 空值 / summary_detail 分支
  2. save_news_to_cache / load_news_from_cache — 往返与防御
  3. fetch_ai_news 排序 — 字符串排序 Bug 复现与验证
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from datetime import datetime, timezone

from src.ai_news_fetcher import (
    _clean_summary,
    _parse_published,
    save_news_to_cache,
    load_news_from_cache,
    fetch_ai_news,
    NEWS_FILE,
)


# ============================================================================
# 1. _clean_summary — 正常路径
# ============================================================================

class TestCleanSummary:
    """验证摘要清洗：HTML 标签去除、截断、空白压缩。"""

    # --- HTML 剥离 ---
    def test_strips_html_tags(self):
        """含 HTML 标签的摘要应去除标签，保留纯文本。"""
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "summary": "<p>This is <b>bold</b> and <a href='x'>linked</a> text.</p>",
        }.get(key, default)
        del entry.summary_detail  # 确保不走 text/plain 分支

        result = _clean_summary(entry)
        assert "This is bold and linked text." in result
        assert "<p>" not in result
        assert "<b>" not in result

    def test_strips_nested_html(self):
        """嵌套 HTML 标签彻底剥离。"""
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "summary": "<div><span>Nested <em>content</em></span></div>",
        }.get(key, default)
        del entry.summary_detail

        result = _clean_summary(entry)
        assert "Nested content" in result
        assert "<" not in result

    # --- 截断 ---
    def test_truncates_at_500_chars(self):
        """超过 500 字的摘要应截断到不超过 500 字。

        注意：_clean_summary 先截断再 strip，实际长度可能 < 500。
        """
        long_text = "X" * 600
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "summary": f"<p>{long_text}</p>",
        }.get(key, default)
        del entry.summary_detail

        result = _clean_summary(entry)
        # HTML 标签 <p></p> 替换为空格，截断 + strip 后长度 ≤ 500
        assert len(result) <= 500, f"期望 ≤ 500 字，实际 {len(result)}"
        assert len(result) >= 495, f"截断不应过度缩水，实际 {len(result)}"
        assert result == "X" * len(result), "内容应保持为 X 字符"

    def test_short_summary_preserved(self):
        """短于 500 字的摘要保持原样。"""
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "summary": "<p>Short.</p>",
        }.get(key, default)
        del entry.summary_detail

        result = _clean_summary(entry)
        assert result == "Short."

    # --- 空白压缩 ---
    def test_collapses_whitespace(self):
        """多空白应压缩为单个空格。"""
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "summary": "<p>Hello    world\n\n\t  !</p>",
        }.get(key, default)
        del entry.summary_detail

        result = _clean_summary(entry)
        assert result == "Hello world !"

    # --- 空值 / 缺失字段 ---
    def test_empty_summary(self):
        """空 summary 返回空字符串。"""
        entry = MagicMock()
        # feedparser entry.get 回退到 description，均空时返回 "" → _clean_summary 返回 ""
        entry.get.side_effect = lambda key, default="": default
        del entry.summary_detail

        result = _clean_summary(entry)
        assert result == ""

    def test_missing_summary_falls_back_to_description(self):
        """summary 缺失时回退到 description。"""
        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "summary": "",
            "description": "<p>Fallback description</p>",
        }.get(key, default)
        del entry.summary_detail

        result = _clean_summary(entry)
        assert "Fallback description" in result

    # --- summary_detail text/plain 分支 ---
    def test_plain_text_summary_detail(self):
        """summary_detail type=text/plain 时直接取值并截断。"""
        entry = MagicMock()
        entry.summary_detail = {"type": "text/plain", "value": "Plain text " + "A" * 600}
        # get 不被调用，因为 hasattr 先命中
        result = _clean_summary(entry)
        assert result.startswith("Plain text ")
        assert len(result) == 500

    def test_html_summary_detail_falls_back_to_regex(self):
        """summary_detail type=text/html 时走 regex 回退。"""

        class MockEntry:
            def get(self, key, default=""):
                return {"summary": "<p>HTML fallback</p>"}.get(key, default)

        entry = MockEntry()
        entry.summary_detail = {"type": "text/html", "value": "<p>HTML fallback</p>"}

        result = _clean_summary(entry)
        assert "HTML fallback" in result
        assert "<p>" not in result


# ============================================================================
# 2. 缓存读写
# ============================================================================

class TestNewsCache:
    """验证 AI 新闻缓存的读写与防御。"""

    def test_load_nonexistent_file(self, tmp_path, monkeypatch):
        """文件不存在时返回空列表。"""
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", tmp_path / "nonexistent.json")
        result = load_news_from_cache()
        assert result == []

    def test_load_empty_file(self, tmp_path, monkeypatch):
        """空文件返回空列表。"""
        f = tmp_path / "empty.json"
        f.write_text("")
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", f)
        result = load_news_from_cache()
        assert result == []

    def test_load_valid_news(self, tmp_path, monkeypatch):
        """合法 JSON 列表正确加载。"""
        f = tmp_path / "news.json"
        data = [
            {"title": "News 1", "link": "https://a.com", "summary": "Summary 1",
             "source": "X", "published": "2026-05-01"},
        ]
        f.write_text(json.dumps(data))
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", f)
        result = load_news_from_cache()
        assert result == data

    def test_load_non_list_json(self, tmp_path, monkeypatch):
        """JSON 不是列表时返回空。"""
        f = tmp_path / "notlist.json"
        f.write_text('{"key": 1}')
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", f)
        result = load_news_from_cache()
        assert result == []

    def test_load_corrupted_json(self, tmp_path, monkeypatch):
        """损坏的 JSON 返回空。"""
        f = tmp_path / "corrupt.json"
        f.write_text("{broken")
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", f)
        result = load_news_from_cache()
        assert result == []

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        """保存后加载，数据一致。"""
        f = tmp_path / "roundtrip.json"
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", f)
        data = [
            {"title": "T", "link": "https://x.com", "summary": "S",
             "source": "X", "published": "2026-05-04"},
        ]
        save_news_to_cache(data)
        loaded = load_news_from_cache()
        assert loaded == data

    def test_save_creates_parent_dir(self, tmp_path, monkeypatch):
        """保存时自动创建父目录。"""
        f = tmp_path / "deep" / "nested" / "news.json"
        monkeypatch.setattr("src.ai_news_fetcher.NEWS_FILE", f)
        save_news_to_cache([])
        assert f.exists()

    def test_save_custom_path(self, tmp_path):
        """自定义路径保存。"""
        custom = tmp_path / "custom_news.json"
        data = [{"title": "Custom"}]
        save_news_to_cache(data, path=custom)
        assert custom.exists()
        loaded = json.loads(custom.read_text())
        assert loaded == data


# ============================================================================
# 3. fetch_ai_news 排序 — 字符串排序 Bug 复现
# ============================================================================

class TestNewsSorting:
    """验证 fetch_ai_news 的排序逻辑。

    代码审查发现：all_entries.sort(key=lambda e: e["published"], reverse=True)
    按字符串排序在 RSS 日期格式下会导致错误排序。
    """

    def _make_feedparser_mock(self, entries_data, source_title="TestSource"):
        """构造 feedparser.parse 的返回值 mock。"""
        feed = MagicMock()
        feed.bozo = False
        feed.feed = {"title": source_title}

        mock_entries = []
        for d in entries_data:
            entry = MagicMock()
            entry.get.side_effect = lambda key, default="", d=d: d.get(key, default)
            # 默认 summary_detail 不存在（不含 hasattr 检查的 attr）
            # 通过删除来触发 AttributeError → hasattr 返回 False
            mock_entries.append(entry)

        feed.entries = mock_entries
        return feed

    def test_datetime_sort_correct_order(self, monkeypatch):
        """按 datetime 排序：最新 > 中间 > 最旧（全部在 7 天内）。

        验证 _parse_published 使用 parsedate_to_datetime 后，排序按实际时间而非字符串。
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        d1 = (now - timedelta(hours=6)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        d2 = (now - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        d3 = (now - timedelta(days=3)).strftime("%a, %d %b %Y %H:%M:%S +0000")

        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        entries_data = [
            {
                "title": "Newest",
                "link": "https://a.com/3",
                "summary": "Should be first",
                "published": d1,
            },
            {
                "title": "Middle",
                "link": "https://a.com/2",
                "summary": "Should be second",
                "published": d2,
            },
            {
                "title": "Oldest",
                "link": "https://a.com/1",
                "summary": "Should be last",
                "published": d3,
            },
        ]

        feed = self._make_feedparser_mock(entries_data)
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://test/rss"])

        titles = [e["title"] for e in result]
        assert titles[0] == "Newest", f"期望 Newest 排第一，实际: {titles[0]}"
        assert titles[1] == "Middle"
        assert titles[2] == "Oldest"

    def test_sorting_correctly_orders_by_datetime(self, monkeypatch):
        """用 Wed/Thu 组合验证 datetime 排序修复。

        实际时间: Thu May 6 (较新) > Wed May 5 (较旧)
        _parse_published 使用 parsedate_to_datetime → 按实际时间排序，较新的排前面。
        """
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        entries_data = [
            {
                "title": "May 6 Thursday (newer)",
                "link": "https://a.com/2",
                "summary": "Newer article",
                "published": "Thu, 06 May 2026 10:00:00 +0000",
            },
            {
                "title": "May 5 Wednesday (older)",
                "link": "https://a.com/1",
                "summary": "Older article",
                "published": "Wed, 05 May 2026 08:00:00 +0000",
            },
        ]

        feed = self._make_feedparser_mock(entries_data)
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://test/rss"])

        titles = [e["title"] for e in result]
        # 修复后：Thu May 6 应排在 Wed May 5 前面
        assert titles[0] == "May 6 Thursday (newer)", \
            f"datetime 排序失败: 期望 'May 6 Thursday' 在前，实际 {titles[0]} 在前。"
        assert titles[1] == "May 5 Wednesday (older)"

    def test_sorting_cross_year_datetime(self, monkeypatch):
        """跨时间段日期排序：较新的排在较旧的前面。

        _parse_published 解析完整日期，按实际时间排序。
        """
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        d1 = (now - timedelta(days=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        d2 = (now - timedelta(days=5)).strftime("%a, %d %b %Y %H:%M:%S +0000")

        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        entries_data = [
            {
                "title": "Newer article",
                "link": "https://a.com/2",
                "summary": "New article",
                "published": d1,
            },
            {
                "title": "Older article",
                "link": "https://a.com/1",
                "summary": "Old article",
                "published": d2,
            },
        ]

        feed = self._make_feedparser_mock(entries_data)
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://test/rss"])

        titles = [e["title"] for e in result]
        assert titles[0] == "Newer article", \
            f"datetime 排序失败: 期望 'Newer article' 在前，实际 {titles[0]} 在前。"
        assert titles[1] == "Older article"

    def test_sorting_works_correctly_with_same_day_prefix(self, monkeypatch):
        """同星期缩写的日期，字符串排序碰巧正确。

        "Tue, 04 May" > "Tue, 03 May" → 字符串比较在同一前缀下按日期数字比较。
        """
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        entries_data = [
            {
                "title": "May 4 Tuesday",
                "link": "https://a.com/2",
                "summary": "May 4",
                "published": "Tue, 04 May 2026 10:00:00 +0000",
            },
            {
                "title": "May 3 Tuesday",
                "link": "https://a.com/1",
                "summary": "May 3",
                "published": "Tue, 03 May 2026 08:00:00 +0000",
            },
        ]

        feed = self._make_feedparser_mock(entries_data)
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://test/rss"])
        titles = [e["title"] for e in result]
        # 同星期缩写时碰巧正确
        assert titles[0] == "May 4 Tuesday"


# ============================================================================
# 3.5 _parse_published — 直接单元测试
# ============================================================================

class TestSortKey:
    """验证 _parse_published 的日期解析与失败回退逻辑。"""

    # --- 正常解析 ---
    def test_parses_rfc2822_date(self):
        """标准 RFC 2822 日期正确解析为 datetime。"""
        entry = {"published": "Tue, 04 May 2026 10:30:00 +0000"}
        result = _parse_published(entry)
        assert isinstance(result, datetime)
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 4
        assert result.hour == 10
        assert result.minute == 30

    def test_parses_with_timezone_offset(self):
        """带时区偏移的日期正确解析。"""
        entry = {"published": "Mon, 03 May 2026 08:00:00 +0800"}
        result = _parse_published(entry)
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 3

    def test_parses_with_named_timezone(self):
        """带命名时区的日期正确解析（如 GMT、EST）。"""
        entry = {"published": "Fri, 01 Apr 2026 06:00:00 GMT"}
        result = _parse_published(entry)
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 1

    # --- 比较语义 ---
    def test_newer_date_has_greater_parse_published(self):
        """较新日期的 _parse_published 应大于较旧日期。"""
        newer = _parse_published({"published": "Tue, 04 May 2026 10:00:00 +0000"})
        older = _parse_published({"published": "Mon, 03 May 2026 08:00:00 +0000"})
        assert newer > older, f"期望 {newer} > {older}"

    def test_cross_year_comparison(self):
        """跨年比较：2026 年应大于 2025 年。"""
        newer = _parse_published({"published": "Fri, 02 Jan 2026 10:00:00 +0000"})
        older = _parse_published({"published": "Wed, 31 Dec 2025 08:00:00 +0000"})
        assert newer > older, f"跨年比较失败: {newer} <= {older}"

    def test_same_date_time_differs(self):
        """同日期不同时间，较晚时间的 key 更大。"""
        later = _parse_published({"published": "Tue, 04 May 2026 23:59:00 +0000"})
        earlier = _parse_published({"published": "Tue, 04 May 2026 00:01:00 +0000"})
        assert later > earlier

    # --- 失败回退到 datetime.min ---
    def test_empty_published_returns_datetime_min(self):
        """空字符串 published → 返回 datetime.min（带 UTC 时区）。"""
        entry = {"published": ""}
        result = _parse_published(entry)
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_missing_published_key_returns_datetime_min(self):
        """缺少 published 键 → 返回 datetime.min（带 UTC 时区）。"""
        entry = {}
        result = _parse_published(entry)
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_malformed_date_returns_datetime_min(self):
        """无效日期格式 → 返回 datetime.min（带 UTC 时区）。"""
        entry = {"published": "not-a-date-at-all"}
        result = _parse_published(entry)
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_gibberish_published_returns_datetime_min(self):
        """乱码 published → 返回 datetime.min（带 UTC 时区）。"""
        entry = {"published": "!@#$%^&*()"}
        result = _parse_published(entry)
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    def test_numeric_only_published_returns_datetime_min(self):
        """纯数字 published → 返回 datetime.min（带 UTC 时区）。"""
        entry = {"published": "1234567890"}
        result = _parse_published(entry)
        assert result == datetime.min.replace(tzinfo=timezone.utc)

    # --- datetime.min 排在末尾 ---
    def test_datetime_min_sorts_after_valid_dates(self):
        """datetime.min 应小于任何有效日期，reverse=True 时排末尾。"""
        valid = _parse_published({"published": "Tue, 04 May 2026 10:00:00 +0000"})
        invalid = _parse_published({"published": ""})
        assert valid > invalid, \
            f"有效日期 key ({valid}) 应大于 datetime.min ({invalid})"


# ============================================================================
# 4. fetch_ai_news — 容错
# ============================================================================

class TestFetchAiNewsResilience:
    """验证 fetch_ai_news 对失败源的容错。"""

    def test_single_source_failure_does_not_block_others(self, monkeypatch):
        """一个源抓取失败不影响其他源。"""
        from datetime import datetime, timedelta, timezone
        recent_date = (datetime.now(timezone.utc) - timedelta(hours=6)).strftime(
            "%a, %d %b %Y %H:%M:%S +0000"
        )

        mock_resp = MagicMock()
        mock_resp.text = ""

        call_count = [0]

        def mock_robust_get(url, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Network failure")
            return mock_resp

        monkeypatch.setattr("src.ai_news_fetcher.robust_get", mock_robust_get)

        # 第二个源返回数据
        feed = MagicMock()
        feed.bozo = False
        feed.feed = {"title": "Good Source"}

        entry = MagicMock()
        entry.get.side_effect = lambda key, default="": {
            "title": "Good article",
            "link": "https://good.com",
            "summary": "Content",
            "published": recent_date,
        }.get(key, default)

        feed.entries = [entry]
        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://bad/rss", "https://good/rss"])
        assert len(result) > 0, "应成功返回好源的数据"
        assert result[0]["title"] == "Good article"

    def test_bozo_feed_without_entries_skipped(self, monkeypatch):
        """解析警告且无 entry 的源被跳过。"""
        mock_resp = MagicMock()
        mock_resp.text = ""
        monkeypatch.setattr("src.ai_news_fetcher.robust_get", lambda *a, **kw: mock_resp)

        feed = MagicMock()
        feed.bozo = True
        feed.bozo_exception = Exception("Parse error")
        feed.entries = []  # 无条目
        feed.feed = {"title": "Broken"}

        monkeypatch.setattr("src.ai_news_fetcher.feedparser.parse", lambda text: feed)

        result = fetch_ai_news(sources=["https://broken/rss"])
        assert result == [], "bozo + 无条目应返回空列表"

    def test_empty_sources_list(self, monkeypatch):
        """空源列表返回空列表。"""
        result = fetch_ai_news(sources=[])
        assert result == []
