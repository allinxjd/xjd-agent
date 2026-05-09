"""Tests for MessageCoordinator."""

import pytest
from agent.company.intent_classifier import IntentClassifier
from agent.company.message import CompanyMessage
from agent.company.message_router import MessageCoordinator, MessagePriority


@pytest.fixture
def coordinator():
    classifier = IntentClassifier(router=None, locale=None)
    return MessageCoordinator(classifier=classifier, env=None)


def _msg(content: str) -> CompanyMessage:
    return CompanyMessage(content=content, cause_by="HumanDirective", sent_from="User")


class TestRouteUserMessage:
    @pytest.mark.asyncio
    async def test_cancel(self, coordinator):
        decision = await coordinator.route_user_message(_msg("暂停开发"))
        assert decision.action == "cancel_pipeline"
        assert decision.priority == MessagePriority.URGENT

    @pytest.mark.asyncio
    async def test_question(self, coordinator):
        decision = await coordinator.route_user_message(_msg("做完了吗？"))
        assert decision.action == "answer_question"
        assert decision.target_role == "PM"

    @pytest.mark.asyncio
    async def test_supplement(self, coordinator):
        decision = await coordinator.route_user_message(_msg("颜色改成红色"))
        assert decision.action == "inject_supplement"

    @pytest.mark.asyncio
    async def test_new_task_high_confidence(self, coordinator):
        decision = await coordinator.route_user_message(_msg("帮我开发一个登录系统"))
        assert decision.action == "queue_new_task"

    @pytest.mark.asyncio
    async def test_new_task_low_confidence_becomes_supplement(self, coordinator):
        decision = await coordinator.route_user_message(_msg("加个按钮"))
        assert decision.action == "inject_supplement"

    @pytest.mark.asyncio
    async def test_confirm(self, coordinator):
        decision = await coordinator.route_user_message(_msg("确认"))
        assert decision.action == "route_approval"

    @pytest.mark.asyncio
    async def test_empty_message(self, coordinator):
        decision = await coordinator.route_user_message(_msg(""))
        assert decision.action == "ignore"


class TestWaitingApproval:
    @pytest.mark.asyncio
    async def test_confirm_during_approval(self, coordinator):
        decision = await coordinator.route_user_message(
            _msg("没问题"), waiting_approval=True
        )
        assert decision.action == "route_approval"
        assert decision.priority == MessagePriority.URGENT

    @pytest.mark.asyncio
    async def test_feedback_during_approval(self, coordinator):
        decision = await coordinator.route_user_message(
            _msg("标题改大一点"), waiting_approval=True
        )
        assert decision.action == "route_approval"
        assert decision.priority == MessagePriority.HIGH

    @pytest.mark.asyncio
    async def test_cancel_during_approval(self, coordinator):
        decision = await coordinator.route_user_message(
            _msg("算了不做了"), waiting_approval=True
        )
        assert decision.action == "cancel_pipeline"


class TestPendingNewTasks:
    @pytest.mark.asyncio
    async def test_queued_tasks_tracked(self, coordinator):
        await coordinator.route_user_message(_msg("帮我开发一个支付系统"))
        assert len(coordinator.pending_new_tasks) == 1
        assert "支付系统" in coordinator.pending_new_tasks[0].content

    @pytest.mark.asyncio
    async def test_clear_pending(self, coordinator):
        await coordinator.route_user_message(_msg("帮我写一个API"))
        coordinator.clear_pending()
        assert len(coordinator.pending_new_tasks) == 0
