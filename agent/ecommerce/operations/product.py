"""商品管理操作 — 上架/编辑/下架/批量操作.

平台适配器实现具体的页面操作，本模块提供跨平台的通用逻辑:
- 商品数据校验
- 批量操作编排
- 商品状态机
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Optional

from agent.ecommerce.protocol import (
    ErrorCode, OperationResult, Product, ProductStatus,
)

logger = logging.getLogger(__name__)


class ProductValidator:
    """商品数据校验器."""

    REQUIRED_FIELDS = {"title", "price"}

    @classmethod
    def validate(cls, data: dict[str, Any]) -> tuple[bool, list[str]]:
        errors: list[str] = []
        for f in cls.REQUIRED_FIELDS:
            if not data.get(f):
                errors.append(f"缺少必填字段: {f}")
        price = data.get("price")
        if price is not None:
            try:
                p = float(price)
                if p <= 0:
                    errors.append("价格必须大于 0")
            except (TypeError, ValueError):
                errors.append("价格格式无效")
        stock = data.get("stock")
        if stock is not None:
            try:
                s = int(stock)
                if s < 0:
                    errors.append("库存不能为负数")
            except (TypeError, ValueError):
                errors.append("库存格式无效")
        return len(errors) == 0, errors


_TRANSITIONS: dict[ProductStatus, set[ProductStatus]] = {
    ProductStatus.DRAFT: {ProductStatus.REVIEWING},
    ProductStatus.REVIEWING: {ProductStatus.ACTIVE, ProductStatus.REJECTED},
    ProductStatus.ACTIVE: {ProductStatus.INACTIVE},
    ProductStatus.INACTIVE: {ProductStatus.ACTIVE},
    ProductStatus.REJECTED: {ProductStatus.DRAFT},
}


class ProductStateMachine:
    """商品生命周期状态机."""

    @staticmethod
    def can_transition(current: str, target: str) -> bool:
        try:
            cur = ProductStatus(current)
            tgt = ProductStatus(target)
        except ValueError:
            return False
        return tgt in _TRANSITIONS.get(cur, set())

    @staticmethod
    def allowed_transitions(current: str) -> list[str]:
        try:
            cur = ProductStatus(current)
        except ValueError:
            return []
        return [s.value for s in _TRANSITIONS.get(cur, set())]


class ProductManager:
    """跨平台商品操作编排."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def batch_toggle(
        self,
        product_ids: list[str],
        active: bool,
        delay: float = 1.0,
    ) -> OperationResult:
        results: list[dict] = []
        for pid in product_ids:
            r = await self._platform.toggle_product(pid, active)
            results.append({"product_id": pid, "success": r.success, "error": r.error})
            if len(product_ids) > 1:
                await asyncio.sleep(delay)
        succeeded = sum(1 for r in results if r["success"])
        return OperationResult.ok(
            "batch_toggle",
            {"total": len(product_ids), "succeeded": succeeded, "results": results},
        )

    async def validate_and_create(self, data: dict[str, Any]) -> OperationResult:
        ok, errors = ProductValidator.validate(data)
        if not ok:
            return OperationResult.fail("create_product", f"校验失败: {'; '.join(errors)}", ErrorCode.INVALID_PARAMS)
        return await self._platform.create_product(data)
