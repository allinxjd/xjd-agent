"""Tests for Action execution and workspace guard."""

from __future__ import annotations

import pytest

from agent.company.action import Action, _apply_workspace_guard, ALL_ACTIONS


class TestActionDefinitions:
    def test_all_actions_registered(self):
        expected = {
            "UserRequirement", "EvaluateRequirement", "WritePRD", "WriteDesign",
            "WriteCode", "CodeReview", "WriteTest", "RunTest", "DeployPlan",
            "ExecuteDeploy", "ChatReply", "QuickTask",
        }
        assert set(ALL_ACTIONS.keys()) == expected

    def test_write_code_has_tools(self):
        assert ALL_ACTIONS["WriteCode"].tools_filter == ["code", "file", "terminal"]

    def test_write_prd_no_tools(self):
        assert ALL_ACTIONS["WritePRD"].tools_filter == []

    def test_chat_reply_no_tools(self):
        assert ALL_ACTIONS["ChatReply"].tools_filter == []

    def test_quick_task_has_tools_and_rounds(self):
        qt = ALL_ACTIONS["QuickTask"]
        assert "terminal" in qt.tools_filter
        assert qt.max_tool_rounds == 20

    def test_prompt_templates_have_context_placeholder(self):
        for name, action in ALL_ACTIONS.items():
            if action.prompt_template:
                assert "{context}" in action.prompt_template, (
                    f"{name} prompt_template missing {{context}}"
                )


class TestWorkspaceGuard:
    def test_guard_not_applied_without_workspace(self):
        class FakeTool:
            def __init__(self):
                self.handler = None
        class FakeEngine:
            def __init__(self):
                self._tools = {"write_file": FakeTool()}

        engine = FakeEngine()
        original = engine._tools["write_file"].handler
        _apply_workspace_guard(engine, "no workspace here")
        assert engine._tools["write_file"].handler is original

    def test_guard_applied_with_workspace(self, tmp_path):
        called = []
        async def fake_write(**kwargs):
            called.append(kwargs)
            return "ok"

        class FakeTool:
            def __init__(self):
                self.handler = fake_write
        class FakeEngine:
            def __init__(self):
                self._tools = {"write_file": FakeTool()}

        engine = FakeEngine()
        prompt = f"## 项目工作目录\n{tmp_path}\nrest of prompt"
        _apply_workspace_guard(engine, prompt)
        assert engine._tools["write_file"].handler is not fake_write
