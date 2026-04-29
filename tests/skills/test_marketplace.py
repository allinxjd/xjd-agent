"""测试 — 技能市场 (HubClient)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent.skills.marketplace import HubSkillMeta, SkillMarketplace


@pytest.fixture
def tmp_marketplace(tmp_path):
    """创建临时 HubClient 实例."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (tmp_path / "hub_config.json").write_text("{}")
    with patch("agent.core.config.get_home", return_value=tmp_path):
        return SkillMarketplace(
            hub_url="https://example.com",
            skills_dir=skills_dir,
        )


class TestHubSkillMeta:
    def test_defaults(self):
        meta = HubSkillMeta()
        assert meta.name == ""
        assert meta.version == "1.0.0"
        assert meta.downloads == 0

    def test_from_kwargs(self):
        meta = HubSkillMeta(
            name="auto-deploy",
            description="自动部署",
            tags=["deploy", "devops"],
        )
        assert meta.name == "auto-deploy"
        assert "deploy" in meta.tags


class TestSkillMarketplace:
    def test_init(self, tmp_marketplace):
        assert tmp_marketplace._hub_url == "https://example.com"
        assert tmp_marketplace._skills_dir.exists()

    @pytest.mark.asyncio
    async def test_search_by_name(self, tmp_marketplace):
        mock_resp = {
            "status": 200,
            "data": {"skills": [
                {"name": "auto-deploy", "slug": "auto-deploy", "description": "自动部署", "tags": ["deploy"]},
            ]},
        }
        with patch.object(tmp_marketplace, "_request", new_callable=AsyncMock, return_value=mock_resp):
            results = await tmp_marketplace.search("deploy")
            assert len(results) == 1
            assert results[0].name == "auto-deploy"

    @pytest.mark.asyncio
    async def test_search_by_tag(self, tmp_marketplace):
        mock_resp = {
            "status": 200,
            "data": {"skills": [
                {"name": "skill-a", "slug": "skill-a", "description": "A", "tags": ["web"]},
            ]},
        }
        with patch.object(tmp_marketplace, "_request", new_callable=AsyncMock, return_value=mock_resp):
            results = await tmp_marketplace.search(tags=["web"])
            assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_empty(self, tmp_marketplace):
        mock_resp = {"status": 200, "data": {"skills": []}}
        with patch.object(tmp_marketplace, "_request", new_callable=AsyncMock, return_value=mock_resp):
            results = await tmp_marketplace.search()
            assert results == []

    @pytest.mark.asyncio
    async def test_install_download_fail(self, tmp_marketplace):
        mock_resp = {"status": 404, "data": {"error": "Not found"}}
        with patch.object(tmp_marketplace, "_request", new_callable=AsyncMock, return_value=mock_resp):
            result = await tmp_marketplace.install("nonexistent")
            assert result.success is False

    @pytest.mark.asyncio
    async def test_uninstall(self, tmp_marketplace):
        skill_dir = tmp_marketplace._skills_dir / "test-skill"
        skill_dir.mkdir()
        (skill_dir / ".hub_meta.json").write_text("{}")
        assert await tmp_marketplace.uninstall("test-skill") is True
        assert not skill_dir.exists()

    @pytest.mark.asyncio
    async def test_uninstall_nonexistent(self, tmp_marketplace):
        assert await tmp_marketplace.uninstall("nope") is False

    def test_list_installed(self, tmp_marketplace):
        for name in ("skill-a", "skill-b"):
            d = tmp_marketplace._skills_dir / name
            d.mkdir()
            (d / ".hub_meta.json").write_text("{}")
        installed = tmp_marketplace.list_installed()
        assert "skill-a" in installed
        assert "skill-b" in installed

    @pytest.mark.asyncio
    async def test_publish(self, tmp_marketplace):
        skill_dir = tmp_marketplace._skills_dir / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: 我的技能\ntags:\n  - custom\n---\n# My Skill\n"
        )
        mock_resp = {"status": 201, "data": {"skill_id": "123", "slug": "my-skill"}}
        with patch.object(tmp_marketplace, "_request", new_callable=AsyncMock, return_value=mock_resp):
            result = await tmp_marketplace.publish(str(skill_dir))
            assert result.success is True

    @pytest.mark.asyncio
    async def test_publish_nonexistent(self, tmp_marketplace):
        result = await tmp_marketplace.publish("/nonexistent/skill")
        assert result.success is False
