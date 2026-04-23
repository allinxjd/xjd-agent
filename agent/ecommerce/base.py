"""电商平台适配器抽象基类.

所有平台适配器 (PDD, Taobao, JD, ...) 继承此类，
实现统一的商品/订单/营销/分析/客服操作接口。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

from agent.ecommerce.protocol import OperationResult, ErrorCode

logger = logging.getLogger(__name__)


class EcommercePlatform(ABC):
    """电商平台适配器基类.

    子类实现具体的浏览器自动化操作。
    所有方法返回 OperationResult，失败时附带 error_code + instruction。
    """

    platform_name: str = ""

    def __init__(self, session_manager: Any = None) -> None:
        self._session = session_manager

    # ── 认证 ──

    @abstractmethod
    async def login(self, credentials: dict[str, Any]) -> OperationResult:
        """登录平台 (扫码/账密)."""
        ...

    @abstractmethod
    async def check_session(self) -> bool:
        """检查当前会话是否有效."""
        ...

    # ── 商品管理 ──

    async def list_products(
        self, filters: Optional[dict[str, Any]] = None,
    ) -> OperationResult:
        return OperationResult.fail(
            "list_products", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def get_product(self, product_id: str) -> OperationResult:
        return OperationResult.fail(
            "get_product", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def create_product(self, product: dict[str, Any]) -> OperationResult:
        return OperationResult.fail(
            "create_product", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def update_product(
        self, product_id: str, updates: dict[str, Any],
    ) -> OperationResult:
        return OperationResult.fail(
            "update_product", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def toggle_product(
        self, product_id: str, active: bool,
    ) -> OperationResult:
        return OperationResult.fail(
            "toggle_product", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    # ── 订单管理 ──

    async def list_orders(
        self, filters: Optional[dict[str, Any]] = None,
    ) -> OperationResult:
        return OperationResult.fail(
            "list_orders", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def get_order(self, order_id: str) -> OperationResult:
        return OperationResult.fail(
            "get_order", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def ship_order(
        self, order_id: str, tracking: dict[str, Any],
    ) -> OperationResult:
        return OperationResult.fail(
            "ship_order", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    # ── 营销推广 ──

    async def list_promotions(self) -> OperationResult:
        return OperationResult.fail(
            "list_promotions", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def create_promotion(
        self, promo: dict[str, Any],
    ) -> OperationResult:
        return OperationResult.fail(
            "create_promotion", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    # ── 数据分析 ──

    async def get_shop_stats(
        self, date_range: Optional[dict[str, Any]] = None,
    ) -> OperationResult:
        return OperationResult.fail(
            "get_shop_stats", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def get_product_stats(self, product_id: str) -> OperationResult:
        return OperationResult.fail(
            "get_product_stats", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    # ── 客服 ──

    async def list_messages(
        self, filters: Optional[dict[str, Any]] = None,
    ) -> OperationResult:
        return OperationResult.fail(
            "list_messages", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    async def reply_message(
        self, msg_id: str, content: str,
    ) -> OperationResult:
        return OperationResult.fail(
            "reply_message", "未实现", ErrorCode.NOT_IMPLEMENTED,
        )

    # ── 生命周期 ──

    async def close(self) -> None:
        """释放资源."""
        pass
