"""测试 — 多配置档管理."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.core.profile import ProfileManager, ProfileInfo


@pytest.fixture
def pm(tmp_path):
    """创建临时 ProfileManager."""
    return ProfileManager(base_dir=tmp_path)


class TestProfileManager:
    def test_default_active(self, pm):
        assert pm.active_profile == "default"

    def test_create(self, pm):
        assert pm.create("work") is True
        profiles = pm.list_profiles()
        names = [p.name for p in profiles]
        assert "work" in names

    def test_create_duplicate(self, pm):
        pm.create("work")
        assert pm.create("work") is False

    def test_switch(self, pm):
        pm.create("work")
        assert pm.switch("work") is True
        assert pm.active_profile == "work"

    def test_switch_nonexistent(self, pm):
        assert pm.switch("nope") is False

    def test_switch_default(self, pm):
        pm.create("work")
        pm.switch("work")
        pm.switch("default")
        assert pm.active_profile == "default"

    def test_list_profiles(self, pm):
        pm.create("work", description="工作环境")
        pm.create("personal")
        profiles = pm.list_profiles()
        assert len(profiles) == 3  # default + work + personal

    def test_delete(self, pm):
        pm.create("temp")
        assert pm.delete("temp") is True
        names = [p.name for p in pm.list_profiles()]
        assert "temp" not in names

    def test_delete_default(self, pm):
        assert pm.delete("default") is False

    def test_delete_active_switches_to_default(self, pm):
        pm.create("work")
        pm.switch("work")
        pm.delete("work")
        assert pm.active_profile == "default"

    def test_export_import(self, pm, tmp_path):
        pm.create("exportable")
        (pm._profiles_dir / "exportable" / "config" / "test.yaml").write_text("key: value")

        archive = str(tmp_path / "export.tar.gz")
        assert pm.export_profile("exportable", archive) is True

        pm.delete("exportable")
        assert pm.import_profile(archive, "imported") is True
        names = [p.name for p in pm.list_profiles()]
        assert "imported" in names

    def test_get_profile_dir(self, pm):
        pm.create("work")
        pm.switch("work")
        d = pm.get_profile_dir("skills")
        assert d.exists()
        assert "work" in str(d)

    def test_get_profile_dir_default(self, pm):
        d = pm.get_profile_dir("skills")
        assert d.exists()


class TestToolsetComposition:
    def test_create_and_apply_toolset(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("tool_a", "A", {}, lambda: "a", category="code")
        reg.register("tool_b", "B", {}, lambda: "b", category="web")
        reg.register("tool_c", "C", {}, lambda: "c", category="code")

        reg.create_toolset("code_only", ["tool_a", "tool_c"])
        count = reg.apply_toolset("code_only")
        assert count == 2
        assert reg.get("tool_a").enabled is True
        assert reg.get("tool_b").enabled is False
        assert reg.get("tool_c").enabled is True

    def test_reset_toolset(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("tool_a", "A", {}, lambda: "a")
        reg.register("tool_b", "B", {}, lambda: "b")
        reg.create_toolset("minimal", ["tool_a"])
        reg.apply_toolset("minimal")
        reg.reset_toolset()
        assert reg.get("tool_b").enabled is True

    def test_list_toolsets(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.create_toolset("set1", ["a", "b"])
        reg.create_toolset("set2", ["c"])
        ts = reg.list_toolsets()
        assert "set1" in ts
        assert "set2" in ts

    def test_apply_nonexistent_toolset(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        assert reg.apply_toolset("nope") == -1

    def test_get_categories(self):
        from agent.tools.registry import ToolRegistry

        reg = ToolRegistry()
        reg.register("a", "A", {}, lambda: "a", category="code")
        reg.register("b", "B", {}, lambda: "b", category="web")
        cats = reg.get_categories()
        assert "code" in cats
        assert "web" in cats


class TestNewTerminalBackends:
    def test_daytona_name(self):
        from agent.core.terminal import DaytonaBackend
        b = DaytonaBackend(workspace="my-ws")
        assert "daytona" in b.name
        assert "my-ws" in b.name

    def test_singularity_name(self):
        from agent.core.terminal import SingularityBackend
        b = SingularityBackend(image="ubuntu.sif")
        assert "singularity" in b.name
