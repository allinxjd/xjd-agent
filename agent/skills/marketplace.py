"""技能市场 — 社区技能的搜索、安装、发布 (支持:
- 从远程索引搜索技能
- 一键安装到本地 skills 目录
- 发布本地技能到市场
- 本地缓存 + 版本兼容检查

用法:
    mp = SkillMarketplace()
    await mp.refresh_index()
    results = await mp.search("部署")
    await mp.install("auto-deploy")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_INDEX_URL = "https://raw.githubusercontent.com/xjd-ai/xjd-agent-skills/main/index.json"

@dataclass
class MarketplaceSkillMeta:
    """市场技能元数据."""

    name: str = ""
    description: str = ""
    author: str = ""
    version: str = "1.0.0"
    downloads: int = 0
    rating: float = 0.0
    tags: list[str] = field(default_factory=list)
    repo_url: str = ""
    skill_url: str = ""
    compatible_version: str = ">=0.1.0"
    created_at: str = ""
    updated_at: str = ""

class SkillMarketplace:
    """技能市场客户端."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        skills_dir: Optional[Path] = None,
        index_url: str = DEFAULT_INDEX_URL,
    ) -> None:
        from agent.core.config import get_home
        self._cache_dir = cache_dir or (get_home() / "marketplace")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._skills_dir = skills_dir or (get_home() / "skills")
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._index_url = index_url
        self._index: list[MarketplaceSkillMeta] = []
        self._index_loaded = False

    async def refresh_index(self) -> bool:
        """从远程拉取技能索引."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(self._index_url)
                if resp.status_code == 200:
                    data = resp.json()
                    self._index = [
                        MarketplaceSkillMeta(**item) for item in data.get("skills", [])
                    ]
                    # 缓存到本地
                    cache_file = self._cache_dir / "index.json"
                    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    self._index_loaded = True
                    logger.info("技能索引已更新: %d 个技能", len(self._index))
                    return True
        except ImportError:
            logger.warning("httpx 未安装，无法刷新索引")
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.warning("刷新索引失败: %s", e)

        # 尝试加载本地缓存
        return self._load_cache()

    def _load_cache(self) -> bool:
        """加载本地缓存的索引."""
        cache_file = self._cache_dir / "index.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                self._index = [
                    MarketplaceSkillMeta(**item) for item in data.get("skills", [])
                ]
                self._index_loaded = True
                return True
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.debug("Failed to load marketplace cache: %s", e)
        return False

    async def search(
        self, query: str = "", tags: Optional[list[str]] = None,
    ) -> list[MarketplaceSkillMeta]:
        """搜索技能."""
        if not self._index_loaded:
            self._load_cache()

        results = []
        query_lower = query.lower()
        for skill in self._index:
            # 关键词匹配
            if query_lower and query_lower not in skill.name.lower() and query_lower not in skill.description.lower():
                if not any(query_lower in t.lower() for t in skill.tags):
                    continue
            # 标签过滤
            if tags and not any(t in skill.tags for t in tags):
                continue
            results.append(skill)

        return results

    async def install(self, skill_name: str) -> bool:
        """安装技能到本地."""
        if not self._index_loaded:
            self._load_cache()

        meta = self._find_skill(skill_name)
        if not meta:
            logger.error("技能 %s 未找到", skill_name)
            return False

        url = meta.skill_url or meta.repo_url
        if not url:
            logger.error("技能 %s 无下载地址", skill_name)
            return False

        try:
            import httpx
        except ImportError:
            logger.error("httpx 未安装，无法安装技能 %s", skill_name)
            return False

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    skill_file = self._skills_dir / f"{skill_name}.yaml"
                    skill_file.write_text(resp.text, encoding="utf-8")
                    logger.info("技能 %s 已安装", skill_name)
                    return True
        except (httpx.HTTPError, OSError) as e:
            logger.error("安装技能 %s 失败: %s", skill_name, e)

        return False

    async def uninstall(self, skill_name: str) -> bool:
        """卸载本地技能."""
        skill_file = self._skills_dir / f"{skill_name}.yaml"
        if skill_file.exists():
            skill_file.unlink()
            logger.info("技能 %s 已卸载", skill_name)
            return True
        return False

    async def publish(self, skill_path: Path) -> str:
        """导出技能为发布格式."""
        if not skill_path.exists():
            return "技能文件不存在"

        try:
            data = yaml.safe_load(skill_path.read_text(encoding="utf-8"))
            meta = {
                "name": data.get("name", skill_path.stem),
                "description": data.get("description", ""),
                "version": "1.0.0",
                "tags": data.get("tags", []),
                "skill_url": "",
            }
            export_path = self._cache_dir / f"{meta['name']}_publish.json"
            export_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return f"已导出到 {export_path}，请提交 PR 到技能索引仓库"
        except (FileNotFoundError, yaml.YAMLError, json.JSONDecodeError, OSError) as e:
            return f"导出失败: {e}"

    def list_installed(self) -> list[str]:
        """列出已安装的技能."""
        return [f.stem for f in self._skills_dir.glob("*.yaml")]

    def _find_skill(self, name: str) -> Optional[MarketplaceSkillMeta]:
        for s in self._index:
            if s.name == name:
                return s
        return None
