"""电商运营工具 — 店铺管理操作注册到 ToolRegistry.

每个工具接收 platform 参数，内部路由到对应平台适配器。
与 ecommerce_tools.py (做图) 和 ecommerce/tools.py (客户端 stub) 互补，
本模块面向卖家/商家的店铺运营操作。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_platform_instances: dict[str, Any] = {}


def _get_platform(platform: str) -> Any:
    if platform in _platform_instances:
        return _platform_instances[platform]
    from agent.ecommerce.platforms import get_platform_class
    cls = get_platform_class(platform)
    if not cls:
        return None
    from agent.ecommerce.session import get_session_manager
    instance = cls(session_manager=get_session_manager())
    _platform_instances[platform] = instance
    return instance


def _result_json(result: Any) -> str:
    if hasattr(result, "to_dict"):
        return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    return str(result)


def _parse_json_param(value: Any) -> tuple[Any, str]:
    """安全解析 JSON 字符串参数，返回 (data, error)."""
    if not isinstance(value, str):
        return value, ""
    try:
        return json.loads(value), ""
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"JSON 解析失败: {e}"


def _no_platform(name: str) -> str:
    return json.dumps({"success": False, "error": f"未知平台: {name}"})


async def _login(platform: str, credentials: str = "") -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    creds, err = _parse_json_param(credentials) if credentials else ({}, "")
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await p.login(creds))


async def _list_products(platform: str, status: str = "", page: int = 1) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    if page > 1:
        filters["page"] = page
    return _result_json(await p.list_products(filters))


async def _get_product(platform: str, product_id: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.get_product(product_id))


async def _create_product(platform: str, product_data: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(product_data)
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await p.create_product(data))


async def _update_product(platform: str, product_id: str, updates: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(updates)
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await p.update_product(product_id, data))


async def _toggle_product(platform: str, product_id: str, active: bool = True) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.toggle_product(product_id, active))


async def _list_orders(platform: str, status: str = "", page: int = 1) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    filters: dict[str, Any] = {}
    if status:
        filters["status"] = status
    if page > 1:
        filters["page"] = page
    return _result_json(await p.list_orders(filters))


async def _ship_order(
    platform: str, order_id: str, tracking_number: str = "", carrier: str = "",
) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.ship_order(order_id, {
        "tracking_number": tracking_number, "carrier": carrier,
    }))


async def _shop_stats(
    platform: str, start_date: str = "", end_date: str = "",
) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    dr: dict[str, str] = {}
    if start_date:
        dr["start"] = start_date
    if end_date:
        dr["end"] = end_date
    return _result_json(await p.get_shop_stats(dr))


async def _list_messages(platform: str, page: int = 1) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.list_messages({"page": page} if page > 1 else {}))


async def _reply_message(platform: str, msg_id: str, content: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.reply_message(msg_id, content))


async def _create_promotion(platform: str, promo_data: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    data, err = _parse_json_param(promo_data)
    if err:
        return json.dumps({"success": False, "error": err})
    return _result_json(await p.create_promotion(data))


async def _list_promotions(platform: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.list_promotions())


async def _get_product_stats(platform: str, product_id: str) -> str:
    p = _get_platform(platform)
    if not p:
        return _no_platform(platform)
    return _result_json(await p.get_product_stats(product_id))


async def _list_platforms_handler() -> str:
    from agent.ecommerce.platforms import list_platforms
    return json.dumps({"platforms": list_platforms()}, ensure_ascii=False)


# ── 注册 ──

_PLATFORM_PARAM = {
    "type": "string",
    "description": "电商平台 (pdd/taobao/jd/douyin)",
}


def register_ecommerce_ops_tools(registry: ToolRegistry) -> None:
    """注册电商运营工具."""

    registry.register(
        name="ecommerce_login",
        description="登录电商平台商家后台",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "credentials": {"type": "string", "description": "JSON 凭证 (可选)"},
            },
            "required": ["platform"],
        },
        handler=_login,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_list_products",
        description="查看店铺商品列表",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "status": {"type": "string", "description": "筛选状态"},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["platform"],
        },
        handler=_list_products,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_get_product",
        description="查看商品详情",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string", "description": "商品 ID"},
            },
            "required": ["platform", "product_id"],
        },
        handler=_get_product,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_create_product",
        description="发布新商品到店铺",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_data": {"type": "string", "description": "商品信息 JSON"},
            },
            "required": ["platform", "product_data"],
        },
        handler=_create_product,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_update_product",
        description="编辑已有商品信息",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string"},
                "updates": {"type": "string", "description": "更新内容 JSON"},
            },
            "required": ["platform", "product_id", "updates"],
        },
        handler=_update_product,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_toggle_product",
        description="商品上架/下架",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string"},
                "active": {"type": "boolean", "description": "true=上架, false=下架"},
            },
            "required": ["platform", "product_id", "active"],
        },
        handler=_toggle_product,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_list_orders",
        description="查看店铺订单列表",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "status": {"type": "string", "description": "订单状态筛选"},
                "page": {"type": "integer", "default": 1},
            },
            "required": ["platform"],
        },
        handler=_list_orders,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_ship_order",
        description="订单发货 (填写物流单号)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "order_id": {"type": "string"},
                "tracking_number": {"type": "string", "description": "物流单号"},
                "carrier": {"type": "string", "description": "快递公司"},
            },
            "required": ["platform", "order_id"],
        },
        handler=_ship_order,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_shop_stats",
        description="查看店铺经营数据 (流量/转化/营收)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "start_date": {"type": "string", "description": "开始日期 YYYY-MM-DD"},
                "end_date": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            },
            "required": ["platform"],
        },
        handler=_shop_stats,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_list_messages",
        description="查看店铺客服消息",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "page": {"type": "integer", "default": 1},
            },
            "required": ["platform"],
        },
        handler=_list_messages,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_reply_message",
        description="回复客服消息",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "msg_id": {"type": "string"},
                "content": {"type": "string", "description": "回复内容"},
            },
            "required": ["platform", "msg_id", "content"],
        },
        handler=_reply_message,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_create_promotion",
        description="创建营销活动/优惠券",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "promo_data": {"type": "string", "description": "活动配置 JSON"},
            },
            "required": ["platform", "promo_data"],
        },
        handler=_create_promotion,
        category="ecommerce_ops",
        requires_approval=True,
    )

    registry.register(
        name="ecommerce_list_promotions",
        description="查看店铺营销活动列表",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
            },
            "required": ["platform"],
        },
        handler=_list_promotions,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_get_product_stats",
        description="查看单品数据统计 (流量/转化/销量)",
        parameters={
            "type": "object",
            "properties": {
                "platform": _PLATFORM_PARAM,
                "product_id": {"type": "string", "description": "商品 ID"},
            },
            "required": ["platform", "product_id"],
        },
        handler=_get_product_stats,
        category="ecommerce_ops",
    )

    registry.register(
        name="ecommerce_list_platforms",
        description="列出所有已支持的电商平台",
        parameters={"type": "object", "properties": {}},
        handler=_list_platforms_handler,
        category="ecommerce_ops",
    )

    logger.info("电商运营工具已注册: 14 个工具")
