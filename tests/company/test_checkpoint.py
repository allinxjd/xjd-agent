"""Tests for CheckpointManager."""

import json
import pytest
from pathlib import Path
from agent.company.checkpoint import CheckpointManager, CheckpointConfig


@pytest.fixture
def tmp_project(tmp_path):
    return tmp_path / "test-project"


@pytest.fixture
def manager(tmp_project):
    tmp_project.mkdir(parents=True, exist_ok=True)
    return CheckpointManager(tmp_project)


class TestCheckpointConfig:
    def test_default_approval_stages(self):
        config = CheckpointConfig()
        assert "PRD" in config.approval_stages
        assert "UIDesign" in config.approval_stages
        assert "Deploy" in config.approval_stages
        assert "Code" not in config.approval_stages

    def test_custom_stages(self):
        config = CheckpointConfig(approval_stages={"PRD", "Code"})
        assert "Code" in config.approval_stages
        assert "Deploy" not in config.approval_stages


class TestNeedsApproval:
    def test_prd_needs_approval(self, manager):
        assert manager.needs_approval("PRD") is True

    def test_code_no_approval(self, manager):
        assert manager.needs_approval("Code") is False

    def test_deploy_needs_approval(self, manager):
        assert manager.needs_approval("Deploy") is True


class TestSaveLoad:
    def test_save_and_load(self, manager, tmp_project):
        stages_done = {"PRD": True, "Code": False}
        stage_outputs = {"PRD": "some prd content"}
        manager.save(stages_done, stage_outputs, "build an app", "task-123", "PRD")

        loaded = manager.load()
        assert loaded is not None
        assert loaded["stages_done"] == stages_done
        assert loaded["stage_outputs"] == stage_outputs
        assert loaded["task_id"] == "task-123"
        assert loaded["waiting_stage"] == "PRD"

    def test_load_nonexistent(self, tmp_project):
        tmp_project.mkdir(parents=True, exist_ok=True)
        mgr = CheckpointManager(tmp_project)
        assert mgr.load() is None

    def test_clear(self, manager, tmp_project):
        manager.save({"PRD": True}, {}, "req", "t1")
        assert manager.load() is not None
        manager.clear()
        assert manager.load() is None


class TestIsWaiting:
    def test_waiting_stage(self, manager):
        manager.save({"PRD": True}, {}, "req", "t1", waiting_stage="UIDesign")
        assert manager.is_waiting() == "UIDesign"

    def test_not_waiting(self, manager):
        manager.save({"PRD": True}, {}, "req", "t1", waiting_stage="")
        assert manager.is_waiting() is None
