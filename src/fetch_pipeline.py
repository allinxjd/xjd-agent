"""
数据管线：统一入口 — 取数据 → 翻译 → 增强 → 附加 source_label → 缓存。

所有 API 路由和调度器只通过此模块获取数据，保证 title_cn/summary_cn/source_label 始终存在。
"""
import logging
from typing import Optional

from src.ai_news_fetcher import fetch_ai_news, save_news_to_cache, load_news_from_cache
from src.skill_fetcher import fetch_skill_projects, save_skill_projects, load_skill_projects
from src.design_fetcher import fetch_design_projects, save_design_projects, load_design_projects
from src.source_label_mapper import get_source_label
from src.translator import translate
from src.github_enricher import batch_enrich

logger = logging.getLogger(__name__)

# 缓存复用阈值：description ≥ 此长度且 summary_cn 已存在则跳过翻译 API
CACHE_REUSE_MIN_DESC_LEN = 150


def _enrich_news(entries: list[dict]) -> list[dict]:
    """为新闻条目附加 title_cn / summary_cn / source_label（调用真实翻译）。"""
    for e in entries:
        title = e.get("title", "")
        if not e.get("title_cn"):
            e["title_cn"] = translate(title)
        summary = e.get("summary", "")
        if not e.get("summary_cn"):
            e["summary_cn"] = translate(summary)
        e.setdefault("source_label", get_source_label(e.get("source", "")))
    return entries


def _enhance_and_translate_projects(projects: list[dict], kind: str) -> list[dict]:
    """项目增强管线：翻译 + GitHub API 增强 + source_label。

    缓存复用规则：
    - 若 description 长度 ≥ 150 且 summary_cn 已存在，跳过翻译 API
    - github_enricher 内部也有缓存复用（已有 enrich 字段则跳过）
    """
    if not projects:
        return projects

    interp_key = f"{kind}_interpretation"

    for p in projects:
        name = p.get("name", "")
        description = p.get("description", "")

        # 翻译 title
        if not p.get("title_cn"):
            p["title_cn"] = translate(name)

        # 翻译 description — 缓存复用
        desc_len = len(description) if description else 0
        if not p.get("summary_cn"):
            if desc_len >= CACHE_REUSE_MIN_DESC_LEN:
                # 长描述：用原文做 summary_cn（后续可人工/异步补翻译）
                p["summary_cn"] = description
            else:
                p["summary_cn"] = translate(description)

        # source_label
        p.setdefault("source_label", get_source_label("github.com"))

        # 确保 interpretation 字段存在
        p.setdefault(interp_key, p.get(interp_key, ""))

    # GitHub API 增强（附加 contributors、topics、license 等）
    try:
        projects = batch_enrich(projects)
    except Exception as exc:
        logger.warning("Batch enrich failed (non-fatal): %s", exc)

    logger.info("Enhanced %d %s projects: translated + enriched", len(projects), kind)
    return projects


# ── 公开 API ──

def refresh_all() -> dict[str, int]:
    """全量刷新三个数据源，返回各板块条目数。"""
    result: dict[str, int] = {}

    try:
        news = fetch_ai_news()
        news = _enrich_news(news)
        if news:
            save_news_to_cache(news)
        result["ai_news"] = len(news)
    except Exception as exc:
        logger.error("Pipeline: AI news refresh failed: %s", exc)
        result["ai_news"] = -1

    try:
        projects = fetch_skill_projects()
        projects = _enhance_and_translate_projects(projects, "skill")
        if projects:
            save_skill_projects(projects)
        result["skill_projects"] = len(projects)
    except Exception as exc:
        logger.error("Pipeline: skill projects refresh failed: %s", exc)
        result["skill_projects"] = -1

    try:
        projects = fetch_design_projects()
        projects = _enhance_and_translate_projects(projects, "design")
        if projects:
            save_design_projects(projects)
        result["design_projects"] = len(projects)
    except Exception as exc:
        logger.error("Pipeline: design projects refresh failed: %s", exc)
        result["design_projects"] = -1

    logger.info("Pipeline refresh complete: %s", result)
    return result


def get_ai_news() -> list[dict]:
    """获取 AI 资讯（从缓存读取，保证字段完整）。"""
    entries = load_news_from_cache()
    if not entries:
        return []
    return _enrich_news(entries)


def get_skill_projects() -> list[dict]:
    """获取 Skill 项目（从缓存读取，保证字段完整）。"""
    projects = load_skill_projects()
    if not projects:
        return []
    return _enhance_and_translate_projects(projects, "skill")


def get_design_projects() -> list[dict]:
    """获取设计项目（从缓存读取，保证字段完整）。"""
    projects = load_design_projects()
    if not projects:
        return []
    return _enhance_and_translate_projects(projects, "design")
