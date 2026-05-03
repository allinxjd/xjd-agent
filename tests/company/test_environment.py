"""Tests for CompanyEnvironment message routing."""

from __future__ import annotations

import pytest

from agent.company.environment import CompanyEnvironment
from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole


@pytest.fixture
def env_with_roles():
    env = CompanyEnvironment()
    pm = CompanyRole(name="PM", description="PM", system_prompt="", watch_actions=["UserRequirement"])
    dev = CompanyRole(name="Developer", description="Dev", system_prompt="", watch_actions=["WriteDesign", "WritePRD"])
    reviewer = CompanyRole(name="Reviewer", description="Rev", system_prompt="", watch_actions=["WriteCode"])
    env.add_role(pm)
    env.add_role(dev)
    env.add_role(reviewer)
    return env


class TestPublish:
    @pytest.mark.asyncio
    async def test_broadcast_by_watch_actions(self, env_with_roles):
        env = env_with_roles
        msg = CompanyMessage(content="requirement", cause_by="UserRequirement", sent_from="System")
        await env.publish(msg)

        assert len(env.roles["PM"]._inbox) == 1
        assert len(env.roles["Developer"]._inbox) == 0
        assert len(env.roles["Reviewer"]._inbox) == 0

    @pytest.mark.asyncio
    async def test_directed_delivery(self, env_with_roles):
        env = env_with_roles
        msg = CompanyMessage(content="fix this", cause_by="CodeReview", sent_from="Reviewer", send_to="Developer")
        await env.publish(msg)

        assert len(env.roles["Developer"]._inbox) == 1
        assert len(env.roles["PM"]._inbox) == 0

    @pytest.mark.asyncio
    async def test_sender_excluded(self, env_with_roles):
        env = env_with_roles
        msg = CompanyMessage(content="self", cause_by="UserRequirement", sent_from="PM")
        await env.publish(msg)

        assert len(env.roles["PM"]._inbox) == 0

    @pytest.mark.asyncio
    async def test_message_log(self, env_with_roles):
        env = env_with_roles
        msg = CompanyMessage(content="test", cause_by="UserRequirement", sent_from="System")
        await env.publish(msg)

        assert len(env.message_log) == 1
        assert env.message_log[0].content == "test"

    @pytest.mark.asyncio
    async def test_pipeline_queue_intercept(self, env_with_roles):
        env = env_with_roles
        env._pipeline_user_queue = []

        msg = CompanyMessage(content="supplement", cause_by="HumanDirective", sent_from="Human")
        await env.publish(msg)

        assert len(env._pipeline_user_queue) == 1
        assert len(env.roles["PM"]._inbox) == 0

    @pytest.mark.asyncio
    async def test_pipeline_queue_ignores_role_messages(self, env_with_roles):
        env = env_with_roles
        env._pipeline_user_queue = []

        msg = CompanyMessage(content="status", cause_by="HumanDirective", sent_from="PM")
        await env.publish(msg)

        assert len(env._pipeline_user_queue) == 0


class TestIsIdle:
    def test_idle_when_no_messages(self, env_with_roles):
        assert env_with_roles.is_idle() is True

    @pytest.mark.asyncio
    async def test_not_idle_with_pending(self, env_with_roles):
        env = env_with_roles
        env.roles["PM"].put_message(CompanyMessage(content="hi", cause_by="UserRequirement"))
        assert env.is_idle() is False


class TestRoleManagement:
    def test_add_and_remove(self):
        env = CompanyEnvironment()
        role = CompanyRole(name="PM", description="test", system_prompt="")
        env.add_role(role)
        assert "PM" in env.roles

        env.remove_role("PM")
        assert "PM" not in env.roles


class TestChatBridge:
    @pytest.mark.asyncio
    async def test_chat_bridge_receives_messages(self, env_with_roles):
        from agent.company.chat_bridge import ChatBridge

        class FakeBridge(ChatBridge):
            def __init__(self):
                self.messages = []
                self.env = None

            async def start(self): pass
            async def stop(self): pass
            async def mirror_to_chat(self, msg):
                self.messages.append(msg)
            def set_environment(self, env):
                self.env = env

        env = env_with_roles
        bridge = FakeBridge()
        env.chat_bridge = bridge

        msg = CompanyMessage(content="hello", cause_by="UserRequirement", sent_from="System")
        await env.publish(msg)

        assert len(bridge.messages) == 1
        assert bridge.messages[0].content == "hello"

    @pytest.mark.asyncio
    async def test_chat_bridge_on_pipeline_queue(self, env_with_roles):
        from agent.company.chat_bridge import ChatBridge

        class FakeBridge(ChatBridge):
            def __init__(self):
                self.messages = []
            async def start(self): pass
            async def stop(self): pass
            async def mirror_to_chat(self, msg):
                self.messages.append(msg)
            def set_environment(self, env): pass

        env = env_with_roles
        bridge = FakeBridge()
        env.chat_bridge = bridge
        env._pipeline_user_queue = []

        msg = CompanyMessage(content="supplement", cause_by="HumanDirective", sent_from="Human")
        await env.publish(msg)

        assert len(bridge.messages) == 1
        assert len(env._pipeline_user_queue) == 1
