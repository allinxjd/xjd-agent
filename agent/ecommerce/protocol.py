"""标准化电商协议 — 统一数据模型 + 错误码.

借鉴 OpenClaw commerce protocol，定义平台无关的数据结构，
所有平台适配器统一返回这些类型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ErrorCode(str, Enum):
    """标准化错误码 — 每个错误码附带 instruction 指引 AI 下一步操作."""

    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    PLATFORM_ERROR = "PLATFORM_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    INVALID_PARAMS = "INVALID_PARAMS"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


ERROR_INSTRUCTIONS: dict[ErrorCode, str] = {
    ErrorCode.AUTH_REQUIRED: "调用 ecommerce_login 进行登录认证",
    ErrorCode.AUTH_EXPIRED: "会话已过期，调用 ecommerce_login 重新认证",
    ErrorCode.RATE_LIMITED: "平台限流，等待 30 秒后重试",
    ErrorCode.CAPTCHA_REQUIRED: "需要验证码，截图发给用户处理",
    ErrorCode.PERMISSION_DENIED: "无权限执行此操作，告知用户检查账号权限",
}


class ProductStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    REVIEWING = "reviewing"
    REJECTED = "rejected"
    DRAFT = "draft"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUND = "refund"


@dataclass
class Product:
    """商品数据模型."""

    product_id: str = ""
    title: str = ""
    price: float = 0.0
    stock: int = 0
    status: str = "active"
    images: list[str] = field(default_factory=list)
    category: str = ""
    platform: str = ""
    platform_url: str = ""
    sku_list: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "title": self.title,
            "price": self.price,
            "stock": self.stock,
            "status": self.status,
            "images": self.images,
            "category": self.category,
            "platform": self.platform,
            "platform_url": self.platform_url,
            "sku_list": self.sku_list,
            "metadata": self.metadata,
        }


@dataclass
class Order:
    """订单数据模型."""

    order_id: str = ""
    status: str = "pending"
    buyer_info: dict[str, Any] = field(default_factory=dict)
    items: list[dict[str, Any]] = field(default_factory=list)
    total_amount: float = 0.0
    shipping_info: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    platform: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "status": self.status,
            "buyer_info": self.buyer_info,
            "items": self.items,
            "total_amount": self.total_amount,
            "shipping_info": self.shipping_info,
            "created_at": self.created_at,
            "platform": self.platform,
            "metadata": self.metadata,
        }


@dataclass
class ShopStats:
    """店铺数据统计."""

    date_range: str = ""
    views: int = 0
    visitors: int = 0
    orders: int = 0
    revenue: float = 0.0
    conversion_rate: float = 0.0
    platform: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date_range": self.date_range,
            "views": self.views,
            "visitors": self.visitors,
            "orders": self.orders,
            "revenue": self.revenue,
            "conversion_rate": self.conversion_rate,
            "platform": self.platform,
            "details": self.details,
        }


@dataclass
class OperationResult:
    """统一操作结果 — 所有平台操作都返回此类型.

    失败时 instruction 字段告诉 AI 下一步该做什么 (OpenClaw 风格)。
    """

    success: bool = False
    action: str = ""
    data: Any = None
    error: str = ""
    error_code: Optional[ErrorCode] = None
    instruction: str = ""

    @staticmethod
    def ok(action: str, data: Any = None) -> OperationResult:
        return OperationResult(success=True, action=action, data=data)

    @staticmethod
    def fail(
        action: str,
        error: str,
        code: Optional[ErrorCode] = None,
    ) -> OperationResult:
        instruction = ""
        if code:
            instruction = ERROR_INSTRUCTIONS.get(code, "")
        return OperationResult(
            success=False,
            action=action,
            error=error,
            error_code=code,
            instruction=instruction,
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "success": self.success,
            "action": self.action,
        }
        if self.data is not None:
            if hasattr(self.data, "to_dict"):
                d["data"] = self.data.to_dict()
            elif isinstance(self.data, list):
                d["data"] = [
                    item.to_dict() if hasattr(item, "to_dict") else item
                    for item in self.data
                ]
            else:
                d["data"] = self.data
        if self.error:
            d["error"] = self.error
        if self.error_code:
            d["error_code"] = self.error_code.value
        if self.instruction:
            d["instruction"] = self.instruction
        return d
