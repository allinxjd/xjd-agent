"""测试 — 技能市场."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.skills.marketplace import MarketplaceSkillMeta, SkillMarketplace


@pytest.fixture
def tmp_marketplace(tmp_path):
    """创建临时市场目录."""
    cache_dir = tmp_path / "marketplace"
    skills_dir = tmp_path / "skills"
    cache_dir.mkdir()
    skills_dir.mkdir()
    return SkillMarketplace(
        cache_dir=cache_dir,
        skills_dir=skills_dir,
        index_url="https://example.com/index.json",
    )


class TestMarketplaceSkillMeta:
    def test_defaults(self):
        meta = MarketplaceSkillMeta()
        assert meta.name == ""
        assert meta.version == "1.0.0"
        assert meta.downloads == 0

    def test_from_dict(self):
        meta = MarketplaceSkillMeta(
            name="auto-deploy",
            description="自动部署",
            author="xjd",
            tags=["deploy", "devops"],
        )
        assert meta.name == "auto-deploy"
        assert "deploy" in meta.tags


class TestSkillMarketplace:
    def test_init(self, tmp_marketplace):
        assert tmp_marketplace._index == []
        assert tmp_marketplace._index_loaded is False

    def test_load_cache(self, tmp_marketplace):
        # 写入缓存
        index_data = {
            "skills": [
                {"name": "test-skill", "description": "测试技能", "tags": ["test"]},
            ]
        }
        cache_file = tmp_marketplace._cache_dir / "index.json"
        cache_file.write_text(json.dumps(index_data))
        assert tmp_marketplace._load_cache() is True
        assert len(tmp_marketplace._index) == 1
        assert tmp_marketplace._index[0].name == "test-skill"

    @pytest.mark.asyncio
    async def test_search_by_name(self, tmp_marketplace):
        tmp_marketplace._index = [
            MarketplaceSkillMeta(name="auto-deploy", description="自动部署", tags=["deploy"]),
            MarketplaceSkillMeta(name="code-review", description="代码审查", tags=["code"]),
        ]
        tmp_marketplace._index_loaded = True
        results = await tmp_marketplace.search("deploy")
        assert len(results) == 1
        assert results[0].name == "auto-deploy"

    @pytest.mark.asyncio
    async def test_search_by_tag(self, tmp_marketplace):
        tmp_marketplace._index = [
            MarketplaceSkillMeta(name="skill-a", description="A", tags=["web"]),
            MarketplaceSkillMeta(name="skill-b", description="B", tags=["code"]),
        ]
        tmp_marketplace._index_loaded = True
        results = await tmp_marketplace.search(tags=["web"])
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_empty_query(self, tmp_marketplace):
        tmp_marketplace._index = [
            MarketplaceSkillMeta(name="a", description="A"),
            MarketplaceSkillMeta(name="b", description="B"),
        ]
        tmp_marketplace._index_loaded = True
        results = await tmp_marketplace.search()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_install_not_found(self, tmp_marketplace):
        tmp_marketplace._index_loaded = True
        result = await tmp_marketplace.install("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_uninstall(self, tmp_marketplace):
        skill_file = tmp_marketplace._skills_dir / "test-skill.yaml"
        skill_file.write_text("name: test-skill")
        assert await tmp_marketplace.uninstall("test-skill") is True
        assert not skill_file.exists()

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent(self, tmp_marketplace):
        assert await tmp_marketplace.uninstall("nope") is False

    def test_list_installed(self, tmp_marketplace):
        (tmp_marketplace._skills_dir / "skill-a.yaml").write_text("name: a")
        (tmp_marketplace._skills_dir / "skill-b.yaml").write_text("name: b")
        installed = tmp_marketplace.list_installed()
        assert "skill-a" in installed
        assert "skill-b" in installed

    @pytest.mark.asyncio
    async def test_publish(self, tmp_marketplace):
        skill_file = tmp_marketplace._skills_dir / "my-skill.yaml"
        skill_file.write_text("name: my-skill\ndescription: 我的技能\ntags:\n  - custom")
        result = await tmp_marketplace.publish(skill_file)
        assert "已导出" in result

    @pytest.mark.asyncio
    async def test_publish_nonexistent(self, tmp_marketplace):
        result = await tmp_marketplace.publish(Path("/nonexistent/skill.yaml"))
        assert "不存在" in result
