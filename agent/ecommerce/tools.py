"""电商工具接口 — Stub 实现 (返回模拟数据，后续接真实 API).

工具分类:
- ecommerce_order: 订单相关
- ecommerce_inventory: 库存相关
- ecommerce_service: 客服相关
- ecommerce_marketing: 营销相关
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register_ecommerce_tools(registry: ToolRegistry) -> None:
    """注册所有电商工具到 ToolRegistry."""

    # ── 订单工具 ──────────────────────────────────────────

    registry.register(
        name="query_order",
        description="查询订单详情 (订单号/手机号)",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单号"},
                "phone": {"type": "string", "description": "手机号 (可选)"},
            },
            "required": ["order_id"],
        },
        handler=_query_order,
        category="ecommerce_order",
    )

    registry.register(
        name="track_shipping",
        description="查询物流追踪信息",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string", "description": "订单号"},
            },
            "required": ["order_id"],
        },
        handler=_track_shipping,
        category="ecommerce_order",
    )

    registry.register(
        name="create_return",
        description="创建退换货申请",
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "reason": {"type": "string", "description": "退换货原因"},
                "return_type": {"type": "string", "enum": ["refund", "exchange"]},
            },
            "required": ["order_id", "reason"],
        },
        handler=_create_return,
        category="ecommerce_order",
    )

    # ── 库存工具 ──────────────────────────────────────────

    registry.register(
        name="check_inventory",
        description="查询商品库存",
        parameters={
            "type": "object",
            "properties": {
                "product_id": {"type": "string", "description": "商品 ID 或 SKU"},
                "warehouse": {"type": "string", "description": "仓库 (可选)"},
            },
            "required": ["product_id"],
        },
        handler=_check_inventory,
        category="ecommerce_inventory",
    )

    registry.register(
        name="search_products",
        description="搜索商品",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "category": {"type": "string", "description": "分类 (可选)"},
                "limit": {"type": "integer", "description": "返回数量", "default": 5},
            },
            "required": ["query"],
        },
        handler=_search_products,
        category="ecommerce_inventory",
    )

    # ── 客服工具 ──────────────────────────────────────────

    registry.register(
        name="create_ticket",
        description="创建客服工单",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            },
            "required": ["user_id", "subject"],
        },
        handler=_create_ticket,
        category="ecommerce_service",
    )

    # ── 营销工具 ──────────────────────────────────────────

    registry.register(
        name="get_recommendations",
        description="获取个性化商品推荐",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "product_id": {"type": "string", "description": "基于此商品推荐 (可选)"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["user_id"],
        },
        handler=_get_recommendations,
        category="ecommerce_marketing",
    )

    registry.register(
        name="query_promotions",
        description="查询当前促销活动和优惠券",
        parameters={
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "category": {"type": "string", "description": "商品分类 (可选)"},
            },
            "required": [],
        },
        handler=_query_promotions,
        category="ecommerce_marketing",
    )

    logger.info("电商工具已注册: 7 个工具")


# ── Stub 实现 (模拟数据) ──────────────────────────────────────

async def _query_order(order_id: str, phone: str = "") -> str:
    """查询订单 (Stub)."""
    return (
        f"订单 {order_id}:\n"
        f"  状态: 已发货\n"
        f"  商品: 无线蓝牙耳机 x1\n"
        f"  金额: ¥299.00\n"
        f"  下单时间: 2026-04-15 14:30\n"
        f"  快递: 顺丰 SF1234567890"
    )


async def _track_shipping(order_id: str) -> str:
    """物流追踪 (Stub)."""
    return (
        f"订单 {order_id} 物流信息 (顺丰 SF1234567890):\n"
        f"  04-16 10:30 已到达 [深圳转运中心]\n"
        f"  04-15 18:00 已揽收 [广州白云区]\n"
        f"  预计 04-17 送达"
    )


async def _create_return(order_id: str, reason: str, return_type: str = "refund") -> str:
    """创建退换货 (Stub)."""
    action = "退款" if return_type == "refund" else "换货"
    return f"已创建{action}申请: 订单 {order_id}, 原因: {reason}。预计 1-3 个工作日处理。"


async def _check_inventory(product_id: str, warehouse: str = "") -> str:
    """查询库存 (Stub)."""
    return (
        f"商品 {product_id} 库存:\n"
        f"  华南仓: 156 件\n"
        f"  华东仓: 89 件\n"
        f"  华北仓: 42 件\n"
        f"  总计: 287 件 (充足)"
    )


async def _search_products(query: str, category: str = "", limit: int = 5) -> str:
    """搜索商品 (Stub)."""
    return (
        f"搜索 \"{query}\" 结果:\n"
        f"  1. 无线蓝牙耳机 Pro — ¥299 (4.8★, 月销 2.3k)\n"
        f"  2. 降噪耳机 Max — ¥599 (4.9★, 月销 1.1k)\n"
        f"  3. 运动蓝牙耳机 — ¥149 (4.6★, 月销 5.6k)"
    )


async def _create_ticket(
    user_id: str, subject: str, description: str = "", priority: str = "medium",
) -> str:
    """创建工单 (Stub)."""
    return f"工单已创建: #{int(time.time()) % 100000}, 主题: {subject}, 优先级: {priority}。客服将在 2 小时内响应。"


async def _get_recommendations(user_id: str, product_id: str = "", limit: int = 5) -> str:
    """商品推荐 (Stub)."""
    return (
        f"为您推荐:\n"
        f"  1. 耳机收纳盒 — ¥29.9 (搭配购买 85% 用户选择)\n"
        f"  2. Type-C 充电线 — ¥19.9\n"
        f"  3. 硅胶耳套替换装 — ¥15.9"
    )


async def _query_promotions(user_id: str = "", category: str = "") -> str:
    """查询促销 (Stub)."""
    return (
        "当前促销活动:\n"
        "  🎉 满 200 减 30 (全场通用)\n"
        "  🎁 新人首单 9 折\n"
        "  💰 数码配件满 3 件 8 折"
    )
