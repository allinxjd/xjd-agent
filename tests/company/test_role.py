"""Tests for CompanyRole lifecycle."""

from __future__ import annotations

import pytest

from agent.company.action import Action
from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole, _clean_model_artifacts


# ── _clean_model_artifacts ───────────────────────────────────

class TestCleanModelArtifacts:
    def test_strips_dsml_tags(self):
        text = "hello <\uff5c\uff5cDSML\uff5c\uff5cfoo>bar</\uff5c\uff5cDSML\uff5c\uff5cfoo> world"
        assert "bar" not in _clean_model_artifacts(text)
        assert "hello" in _clean_model_artifacts(text)
        assert "world" in _clean_model_artifacts(text)

    def test_strips_antml_prefixed_tags(self):
        prefix = "antml"
        tag = "thinking"
        text = f"before <{prefix}:{tag}>inner</{prefix}:{tag}> after"
        result = _clean_model_artifacts(text)
        assert "before" in result
        assert "after" in result
        assert prefix not in result

    def test_collapses_newlines(self):
        text = "a\n\n\n\n\nb"
        result = _clean_model_artifacts(text)
        assert "\n\n\n" not in result

    def test_passthrough_normal(self):
        text = "normal text"
        assert _clean_model_artifacts(text) == "normal text"


# ── CompanyRole basics ───────────────────────────────────────

class TestCompanyRoleBasics:
    def test_put_message_and_has_pending(self):
        role = CompanyRole(name="PM", description="test", system_prompt="")
        assert role.has_pending is False
        role.put_message(CompanyMessage(content="hi", cause_by="UserRequirement"))
        assert role.has_pending is True

    def test_build_system_prompt(self):
        role = CompanyRole(
            name="PM",
            description="test",
            system_prompt="base prompt",
            goal="deliver PRD",
            backstory="experienced PM",
            karpathy_constraints=["keep it simple"],
        )
        prompt = role.build_system_prompt()
        assert "base prompt" in prompt
        assert "deliver PRD" in prompt
        assert "experienced PM" in prompt
        assert "keep it simple" in prompt

    def test_build_system_prompt_empty(self):
        role = CompanyRole(name="PM", description="test", system_prompt="")
        prompt = role.build_system_prompt()
        assert prompt == ""


# ── _observe ─────────────────────────────────────────────────

class TestObserve:
    @pytest.mark.asyncio
    async def test_filters_by_watch_actions(self):
        role = CompanyRole(
            name="Developer",
            description="test",
            system_prompt="",
            watch_actions=["WriteDesign"],
        )
        role.put_message(CompanyMessage(content="design", cause_by="WriteDesign"))
        role.put_message(CompanyMessage(content="prd", cause_by="WritePRD"))

        matched = await role._observe()
        assert len(matched) == 1
        assert matched[0].content == "design"

    @pytest.mark.asyncio
    async def test_directed_message_bypasses_watch(self):
        role = CompanyRole(
            name="Developer",
            description="test",
            system_prompt="",
            watch_actions=["WriteDesign"],
        )
        msg = CompanyMessage(content="fix this", cause_by="CodeReview", send_to="Developer")
        role.put_message(msg)

        matched = await role._observe()
        assert len(matched) == 1
        assert matched[0].content == "fix this"

    @pytest.mark.asyncio
    async def test_dedup_by_msg_id(self):
        role = CompanyRole(
            name="Developer",
            description="test",
            system_prompt="",
            watch_actions=["WriteDesign"],
        )
        msg = CompanyMessage(content="design", cause_by="WriteDesign", msg_id="abc123")
        role.put_message(msg)
        role.put_message(msg)

        matched = await role._observe()
        assert len(matched) == 1


# ── _think ───────────────────────────────────────────────────

class TestThink:
    @pytest.mark.asyncio
    async def test_by_order_sequential(self):
        a1 = Action(name="Step1", description="first")
        a2 = Action(name="Step2", description="second")
        role = CompanyRole(
            name="PM",
            description="test",
            system_prompt="",
            actions=[a1, a2],
            react_mode="by_order",
        )
        role._state = -1

        action = await role._think([])
        assert action.name == "Step1"

        action = await role._think([])
        assert action.name == "Step2"

        action = await role._think([])
        assert action is None

    @pytest.mark.asyncio
    async def test_no_actions(self):
        role = CompanyRole(name="PM", description="test", system_prompt="", actions=[])
        role._state = -1
        action = await role._think([])
        assert action is None
