"""电商运营模块测试."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from agent.ecommerce.protocol import (
    ErrorCode, OperationResult, Order, OrderStatus, Product, ProductStatus, ShopStats,
)


# ── Product Operations ──

class TestProductValidator:
    def test_valid_product(self):
        from agent.ecommerce.operations.product import ProductValidator
        ok, errors = ProductValidator.validate({"title": "测试商品", "price": 29.9})
        assert ok
        assert errors == []

    def test_missing_title(self):
        from agent.ecommerce.operations.product import ProductValidator
        ok, errors = ProductValidator.validate({"price": 29.9})
        assert not ok
        assert any("title" in e for e in errors)

    def test_invalid_price(self):
        from agent.ecommerce.operations.product import ProductValidator
        ok, errors = ProductValidator.validate({"title": "test", "price": -1})
        assert not ok
        assert any("价格" in e for e in errors)

    def test_negative_stock(self):
        from agent.ecommerce.operations.product import ProductValidator
        ok, errors = ProductValidator.validate({"title": "test", "price": 10, "stock": -5})
        assert not ok
        assert any("库存" in e for e in errors)


class TestProductStateMachine:
    def test_valid_transitions(self):
        from agent.ecommerce.operations.product import ProductStateMachine
        assert ProductStateMachine.can_transition("draft", "reviewing")
        assert ProductStateMachine.can_transition("reviewing", "active")
        assert ProductStateMachine.can_transition("active", "inactive")
        assert ProductStateMachine.can_transition("inactive", "active")

    def test_invalid_transitions(self):
        from agent.ecommerce.operations.product import ProductStateMachine
        assert not ProductStateMachine.can_transition("draft", "active")
        assert not ProductStateMachine.can_transition("active", "draft")
        assert not ProductStateMachine.can_transition("rejected", "active")

    def test_allowed_transitions(self):
        from agent.ecommerce.operations.product import ProductStateMachine
        allowed = ProductStateMachine.allowed_transitions("active")
        assert "inactive" in allowed
        assert "draft" not in allowed


# ── Order Operations ──

class TestReturnWorkflow:
    def test_full_return_flow(self):
        from agent.ecommerce.operations.order import ReturnWorkflow
        wf = ReturnWorkflow()
        wf.start("ORD001")
        assert wf.get_state("ORD001") == "requested"
        ok, _ = wf.advance("ORD001", "approved")
        assert ok
        ok, _ = wf.advance("ORD001", "shipped_back")
        assert ok
        ok, _ = wf.advance("ORD001", "received")
        assert ok
        ok, _ = wf.advance("ORD001", "refunded")
        assert ok
        assert wf.get_state("ORD001") == "refunded"

    def test_invalid_transition(self):
        from agent.ecommerce.operations.order import ReturnWorkflow
        wf = ReturnWorkflow()
        wf.start("ORD002")
        ok, msg = wf.advance("ORD002", "refunded")
        assert not ok

    def test_nonexistent_order(self):
        from agent.ecommerce.operations.order import ReturnWorkflow
        wf = ReturnWorkflow()
        ok, msg = wf.advance("NOPE", "approved")
        assert not ok


class TestOrderManager:
    @pytest.mark.asyncio
    async def test_batch_ship(self):
        from agent.ecommerce.operations.order import OrderManager
        mock_platform = AsyncMock()
        mock_platform.ship_order.return_value = OperationResult.ok("ship")
        mgr = OrderManager(mock_platform)
        result = await mgr.batch_ship([
            {"order_id": "O1", "tracking_number": "T1", "carrier": "韵达"},
            {"order_id": "O2", "tracking_number": "T2", "carrier": "中通"},
        ], delay=0)
        assert result.success
        assert result.data["succeeded"] == 2
        assert mock_platform.ship_order.call_count == 2


# ── Analytics Operations ──

class TestDataParser:
    def test_parse_shop_stats(self):
        from agent.ecommerce.operations.analytics import DataParser
        text = "访客数 1,234  订单数 56  营业额 12,345.67  转化率 4.5%"
        stats = DataParser.parse_shop_stats(text, "pdd")
        assert stats.visitors == 1234
        assert stats.orders == 56
        assert stats.revenue == 12345.67
        assert abs(stats.conversion_rate - 0.045) < 0.001

    def test_parse_number(self):
        from agent.ecommerce.operations.analytics import DataParser
        assert DataParser.parse_number("1,234") == 1234.0
        assert DataParser.parse_number("no number") is None


# ── Customer Service Operations ──

class TestMessageClassifier:
    def test_complaint(self):
        from agent.ecommerce.operations.customer import MessageClassifier, MessageCategory
        assert MessageClassifier.classify("我要投诉你们") == MessageCategory.COMPLAINT

    def test_logistics(self):
        from agent.ecommerce.operations.customer import MessageClassifier, MessageCategory
        assert MessageClassifier.classify("什么时候发货") == MessageCategory.LOGISTICS

    def test_post_sale(self):
        from agent.ecommerce.operations.customer import MessageClassifier, MessageCategory
        assert MessageClassifier.classify("我要退货") == MessageCategory.POST_SALE

    def test_pre_sale(self):
        from agent.ecommerce.operations.customer import MessageClassifier, MessageCategory
        assert MessageClassifier.classify("这个多少钱") == MessageCategory.PRE_SALE

    def test_general(self):
        from agent.ecommerce.operations.customer import MessageClassifier, MessageCategory
        assert MessageClassifier.classify("你好") == MessageCategory.GENERAL


class TestPriorityQueue:
    def test_priority_order(self):
        from agent.ecommerce.operations.customer import (
            PriorityQueue, PrioritizedMessage, MessageCategory, _PRIORITY_MAP,
        )
        q = PriorityQueue()
        q.enqueue(PrioritizedMessage(priority=4, timestamp=1.0, uid="u1", content="hi", category=MessageCategory.GENERAL))
        q.enqueue(PrioritizedMessage(priority=0, timestamp=2.0, uid="u2", content="投诉", category=MessageCategory.COMPLAINT))
        q.enqueue(PrioritizedMessage(priority=2, timestamp=3.0, uid="u3", content="物流", category=MessageCategory.LOGISTICS))
        first = q.dequeue()
        assert first.category == MessageCategory.COMPLAINT
        second = q.dequeue()
        assert second.category == MessageCategory.LOGISTICS


class TestKnowledgeBase:
    def test_search_faq(self):
        from agent.ecommerce.operations.customer import KnowledgeBase
        kb = KnowledgeBase()
        kb._faq = [
            {"q": "发货时间", "a": "48小时内发货"},
            {"q": "退货流程", "a": "申请→审核→寄回→退款"},
        ]
        results = kb.search("发货")
        assert len(results) >= 1
        assert "48小时" in results[0]

    def test_transfer_keywords(self):
        from agent.ecommerce.operations.customer import KnowledgeBase
        kb = KnowledgeBase()
        assert kb.should_transfer("我要转人工")
        assert not kb.should_transfer("你好")


# ── Promotion Operations ──

class TestPromotionROI:
    def test_estimate_roi(self):
        from agent.ecommerce.operations.promotion import PromotionManager
        result = PromotionManager.estimate_roi(
            {"budget": 1000, "cpc": 2.0},
            {"conversion_rate": 0.05, "avg_order_value": 100},
        )
        assert result["estimated_clicks"] == 500
        assert result["estimated_orders"] == 25.0
        assert result["estimated_revenue"] == 2500.0
        assert result["estimated_roi"] == 150.0
