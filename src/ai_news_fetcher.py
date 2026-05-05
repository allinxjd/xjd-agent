"""
AI 资讯抓取器：RSS 源解析，返回标准化新闻列表。

依赖 feedparser 解析 RSS/Atom，复用 http_client.robust_get 处理网络。
仅保留最近 7 天的条目。
"""
import logging
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

import feedparser

from src.http_client import robust_get

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
NEWS_FILE = DATA_DIR / "ai_news.json"

# 默认 RSS 源列表（可扩展）
DEFAULT_SOURCES = [
    "https://openai.com/blog/rss.xml",
    "https://huggingface.co/blog/feed.xml",
    "https://www.technologyreview.com/feed/",
]

# 仅保留最近 N 天的新闻
RECENT_DAYS = 7


def _parse_published(entry: dict) -> datetime:
    """将 entry['published'] 解析为 datetime，失败返回 datetime.min（排末尾）。"""
    raw = entry.get("published", "")
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _is_recent(entry: dict, days: int = RECENT_DAYS) -> bool:
    """判断条目是否在最近 N 天内发布。无日期视为近期（保留）。"""
    pub = _parse_published(entry)
    if pub == datetime.min.replace(tzinfo=timezone.utc):
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return pub >= cutoff


def fetch_ai_news(sources: Optional[list[str]] = None) -> list[dict]:
    """从 RSS 源抓取近期 AI 资讯，仅保留最近 7 天，返回标准化列表。

    每条含：title, link, summary, source, published。
    单个源抓取失败不影响其他源。
    """
    if sources is None:
        sources = DEFAULT_SOURCES

    all_entries: list[dict] = []

    for src_url in sources:
        try:
            logger.info("Fetching RSS: %s", src_url)
            resp = robust_get(src_url, timeout=15, max_retries=2)
            feed = feedparser.parse(resp.text)

            if feed.bozo and not feed.entries:
                logger.warning("RSS parse warning for %s: %s", src_url, feed.bozo_exception)
                continue

            raw_count = len(feed.entries)
            source_title = feed.feed.get("title", src_url)
            kept = 0

            for entry in feed.entries:
                raw_entry = {
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                    "summary": _clean_summary(entry),
                    "source": source_title,
                    "published": entry.get("published") or entry.get("updated") or "",
                }
                if _is_recent(raw_entry):
                    all_entries.append(raw_entry)
                    kept += 1

            logger.info("  -> %s: raw=%d kept=%d (7d filter)", source_title, raw_count, kept)

        except Exception as exc:
            logger.error("Failed to fetch %s: %s", src_url, exc)

    # 按发布时间倒序
    all_entries.sort(key=_parse_published, reverse=True)
    logger.info("Total AI news fetched: %d", len(all_entries))
    return all_entries


def _clean_summary(entry) -> str:
    """提取并清理摘要文本（去除 HTML 标签）。"""
    summary = entry.get("summary") or entry.get("description") or ""
    # feedparser 可能已提供纯文本 summary_detail
    if hasattr(entry, "summary_detail") and entry.summary_detail.get("type") == "text/plain":
        return entry.summary_detail.get("value", "")[:500].strip()
    # 简单去 HTML 标签
    import re
    text = re.sub(r"<[^>]+>", " ", summary)
    text = re.sub(r"\s+", " ", text)
    return text[:500].strip()


def save_news_to_cache(news: list[dict], path: Optional[Path] = None) -> None:
    """将新闻列表写入 JSON 缓存文件。"""
    import json
    target = path or NEWS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(news, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Saved %d news to %s", len(news), target)


def load_news_from_cache() -> list[dict]:
    """从 JSON 缓存读取新闻列表（前端 API 使用）。"""
    import json
    if not NEWS_FILE.exists():
        return []
    try:
        text = NEWS_FILE.read_text(encoding="utf-8").strip()
        if not text:
            return []
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.warning("Failed to read ai_news.json: %s", exc)
        return []
