"""电商 Agent 角色定义 — 4 个专业 Agent 角色.

每个角色有独立的 system prompt、工具白名单和关键词匹配规则。
继承 multi_agent.py 的 AgentRole dataclass。
"""

from __future__ import annotations

from agent.core.multi_agent import AgentRole

# ── 订单 Agent ──────────────────────────────────────────────

ORDER_AGENT = AgentRole(
    name="order",
    description="订单管理专家，处理订单查询、物流追踪、退换货",
    system_prompt=(
        "你是电商订单管理专家。你的职责:\n"
        "1. 查询订单状态和详情\n"
        "2. 追踪物流信息\n"
        "3. 处理退换货申请\n"
        "4. 解答订单相关问题\n\n"
        "回答要准确、简洁。涉及退款等敏感操作时，需确认用户身份和订单信息。"
    ),
    tools_filter=["ecommerce_order"],
    max_tool_rounds=10,
    keywords=[
        "订单", "物流", "快递", "发货", "退货", "退款", "换货",
        "运费", "签收", "配送", "order", "shipping", "refund", "tracking",
    ],
)

# ── 库存 Agent ──────────────────────────────────────────────

INVENTORY_AGENT = AgentRole(
    name="inventory",
    description="库存管理专家，处理库存查询、补货预警、SKU 管理",
    system_prompt=(
        "你是电商库存管理专家。你的职责:\n"
        "1. 查询商品库存和可用数量\n"
        "2. 监控库存预警 (低库存/缺货)\n"
        "3. 提供补货建议\n"
        "4. 管理 SKU 和变体信息\n\n"
        "数据要精确，库存数量必须实时查询，不要猜测。"
    ),
    tools_filter=["ecommerce_inventory"],
    max_tool_rounds=8,
    keywords=[
        "库存", "存货", "缺货", "补货", "SKU", "数量", "有货",
        "stock", "inventory", "available", "out of stock",
    ],
)

# ── 客服 Agent ──────────────────────────────────────────────

CUSTOMER_SERVICE_AGENT = AgentRole(
    name="customer_service",
    description="客户服务专家，处理咨询、投诉、售后问题",
    system_prompt=(
        "你是电商客户服务专家。你的职责:\n"
        "1. 解答商品咨询 (规格、材质、使用方法)\n"
        "2. 处理客户投诉和建议\n"
        "3. 协调售后服务\n"
        "4. 提供购物指导\n\n"
        "态度友好专业，优先解决客户问题。复杂问题可升级给人工客服。"
    ),
    tools_filter=["ecommerce_service"],
    max_tool_rounds=10,
    keywords=[
        "咨询", "投诉", "售后", "客服", "问题", "帮助", "怎么用",
        "质量", "保修", "help", "complaint", "support", "warranty",
    ],
)

# ── 营销 Agent ──────────────────────────────────────────────

MARKETING_AGENT = AgentRole(
    name="marketing",
    description="营销推荐专家，处理商品推荐、促销活动、优惠券",
    system_prompt=(
        "你是电商营销推荐专家。你的职责:\n"
        "1. 根据用户偏好推荐商品\n"
        "2. 介绍当前促销活动和优惠\n"
        "3. 提供搭配购买建议\n"
        "4. 管理优惠券和折扣信息\n\n"
        "推荐要个性化，基于用户历史和偏好。不要过度推销。"
    ),
    tools_filter=["ecommerce_marketing"],
    max_tool_rounds=8,
    keywords=[
        "推荐", "优惠", "促销", "折扣", "优惠券", "活动", "搭配",
        "类似", "便宜", "recommend", "discount", "coupon", "sale",
    ],
)

# ── 所有电商角色 ──────────────────────────────────────────────

ECOMMERCE_ROLES = [
    ORDER_AGENT,
    INVENTORY_AGENT,
    CUSTOMER_SERVICE_AGENT,
    MARKETING_AGENT,
]
