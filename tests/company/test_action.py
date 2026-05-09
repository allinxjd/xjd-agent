"""Tests for Action execution and workspace guard."""

from __future__ import annotations

import pytest

from agent.company.action import Action, _apply_workspace_guard, ALL_ACTIONS


class TestActionDefinitions:
    def test_all_actions_registered(self):
        expected = {
            "UserRequirement", "EvaluateRequirement", "WritePRD",
            "WritePrototype", "WriteUIDesign", "WriteDesign",
            "SetupEnv", "WriteCode", "VerifyRun", "CodeReview", "CodeReviewFollowup", "FixCode",
            "WriteTest", "RunTest",
            "DeployPlan", "ExecuteDeploy", "ChatReply", "QuickTask",
        }
        assert set(ALL_ACTIONS.keys()) == expected

    def test_write_code_has_tools(self):
        assert ALL_ACTIONS["WriteCode"].tools_filter == ["code", "file", "terminal"]

    def test_write_code_has_self_verify_rounds(self):
        assert ALL_ACTIONS["WriteCode"].max_tool_rounds == 30

    def test_write_prd_no_tools(self):
        assert ALL_ACTIONS["WritePRD"].tools_filter == []

    def test_chat_reply_no_tools(self):
        assert ALL_ACTIONS["ChatReply"].tools_filter == []

    def test_code_review_has_file_tools(self):
        cr = ALL_ACTIONS["CodeReview"]
        assert "file" in cr.tools_filter
        assert cr.max_tool_rounds == 10

    def test_write_test_has_terminal(self):
        wt = ALL_ACTIONS["WriteTest"]
        assert "terminal" in wt.tools_filter
        assert wt.max_tool_rounds == 15

    def test_quick_task_has_tools_and_rounds(self):
        qt = ALL_ACTIONS["QuickTask"]
        assert "terminal" in qt.tools_filter
        assert qt.max_tool_rounds == 20

    def test_verify_run_has_tools_and_rounds(self):
        vr = ALL_ACTIONS["VerifyRun"]
        assert "terminal" in vr.tools_filter
        assert "code" in vr.tools_filter
        assert "file" in vr.tools_filter
        assert vr.max_tool_rounds == 15

    def test_setup_env_has_tools_and_rounds(self):
        se = ALL_ACTIONS["SetupEnv"]
        assert "terminal" in se.tools_filter
        assert se.max_tool_rounds == 10

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

    @pytest.mark.asyncio
    async def test_write_file_blocked_outside_workspace(self, tmp_path):
        async def fake_write(**kwargs):
            return "ok"

        class FakeTool:
            def __init__(self, handler):
                self.handler = handler
        class FakeEngine:
            def __init__(self):
                self._tools = {
                    "write_file": FakeTool(fake_write),
                }

        engine = FakeEngine()
        prompt = f"## 项目工作目录\n{tmp_path}\nrest"
        _apply_workspace_guard(engine, prompt)

        result = await engine._tools["write_file"].handler(path="/etc/passwd")
        assert "禁止" in result

    @pytest.mark.asyncio
    async def test_write_file_allowed_inside_workspace(self, tmp_path):
        async def fake_write(**kwargs):
            return "ok"

        class FakeTool:
            def __init__(self, handler):
                self.handler = handler
        class FakeEngine:
            def __init__(self):
                self._tools = {
                    "write_file": FakeTool(fake_write),
                }

        engine = FakeEngine()
        prompt = f"## 项目工作目录\n{tmp_path}\nrest"
        _apply_workspace_guard(engine, prompt)

        result = await engine._tools["write_file"].handler(path=str(tmp_path / "src" / "main.py"))
        assert result == "ok"

    def test_terminal_workdir_forced(self, tmp_path):
        called_kwargs = {}
        async def fake_terminal(**kwargs):
            called_kwargs.update(kwargs)
            return "ok"

        class FakeTool:
            def __init__(self, handler):
                self.handler = handler
        class FakeEngine:
            def __init__(self):
                self._tools = {
                    "run_terminal": FakeTool(fake_terminal),
                }

        engine = FakeEngine()
        prompt = f"## 项目工作目录\n{tmp_path}\nrest"
        _apply_workspace_guard(engine, prompt)
        assert engine._tools["run_terminal"].handler is not fake_terminal

    @pytest.mark.asyncio
    async def test_terminal_workdir_overridden(self, tmp_path):
        called_kwargs = {}
        async def fake_terminal(**kwargs):
            called_kwargs.update(kwargs)
            return "ok"

        class FakeTool:
            def __init__(self, handler):
                self.handler = handler
        class FakeEngine:
            def __init__(self):
                self._tools = {
                    "run_terminal": FakeTool(fake_terminal),
                }

        engine = FakeEngine()
        prompt = f"## 项目工作目录\n{tmp_path}\nrest"
        _apply_workspace_guard(engine, prompt)

        await engine._tools["run_terminal"].handler(command="ls", workdir="/tmp")
        assert called_kwargs["workdir"] == str(tmp_path.resolve())
