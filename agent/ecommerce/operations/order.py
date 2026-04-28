"""订单管理操作 — 查看/发货/售后/批量处理.

平台适配器实现具体的页面操作，本模块提供跨平台的通用逻辑:
- 订单状态流转
- 批量发货编排
- 售后工单管理
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum, IntEnum
from typing import Any, Optional

from agent.ecommerce.protocol import (
    ErrorCode, OperationResult, Order, OrderStatus,
)

logger = logging.getLogger(__name__)


class ReturnState(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    SHIPPED_BACK = "shipped_back"
    RECEIVED = "received"
    REFUNDED = "refunded"
    REJECTED = "rejected"


_RETURN_TRANSITIONS: dict[ReturnState, set[ReturnState]] = {
    ReturnState.REQUESTED: {ReturnState.APPROVED, ReturnState.REJECTED},
    ReturnState.APPROVED: {ReturnState.SHIPPED_BACK},
    ReturnState.SHIPPED_BACK: {ReturnState.RECEIVED},
    ReturnState.RECEIVED: {ReturnState.REFUNDED},
}


class ReturnWorkflow:
    """退货退款状态机."""

    def __init__(self) -> None:
        self._states: dict[str, ReturnState] = {}

    def get_state(self, order_id: str) -> Optional[str]:
        s = self._states.get(order_id)
        return s.value if s else None

    def start(self, order_id: str) -> None:
        self._states[order_id] = ReturnState.REQUESTED

    def advance(self, order_id: str, target: str) -> tuple[bool, str]:
        current = self._states.get(order_id)
        if not current:
            return False, "退货工单不存在"
        try:
            tgt = ReturnState(target)
        except ValueError:
            return False, f"无效状态: {target}"
        allowed = _RETURN_TRANSITIONS.get(current, set())
        if tgt not in allowed:
            return False, f"不能从 {current.value} 转到 {tgt.value}"
        self._states[order_id] = tgt
        return True, f"已更新为 {tgt.value}"


class OrderManager:
    """跨平台订单操作编排."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform
        self._return_workflow = ReturnWorkflow()

    async def batch_ship(
        self,
        orders: list[dict[str, str]],
        delay: float = 1.0,
    ) -> OperationResult:
        results: list[dict] = []
        for i, o in enumerate(orders):
            oid = o.get("order_id", "")
            tracking = o.get("tracking_number", "")
            carrier = o.get("carrier", "")
            if not oid or not tracking:
                results.append({"order_id": oid, "success": False, "error": "缺少订单号或快递单号"})
                continue
            r = await self._platform.ship_order(oid, {"tracking_number": tracking, "carrier": carrier})
            results.append({"order_id": oid, "success": r.success, "error": r.error})
            if len(orders) > 1:
                wait = min(delay * (1.5 ** min(i // 10, 3)), 10.0)
                await asyncio.sleep(wait)
        succeeded = sum(1 for r in results if r["success"])
        return OperationResult.ok(
            "batch_ship",
            {"total": len(orders), "succeeded": succeeded, "results": results},
        )

    async def get_pending_shipments(self) -> OperationResult:
        r = await self._platform.list_orders({"status": "paid"})
        if not r.success:
            return r
        pending = []
        if isinstance(r.data, list):
            pending = [o for o in r.data if (o.get("status") if isinstance(o, dict) else getattr(o, "status", "")) in ("paid", "pending")]
        return OperationResult.ok("pending_shipments", pending)

    async def track_status(self, order_ids: list[str]) -> OperationResult:
        results: list[dict] = []
        for oid in order_ids:
            r = await self._platform.get_order(oid)
            if r.success and r.data:
                d = r.data.to_dict() if hasattr(r.data, "to_dict") else r.data
                results.append({"order_id": oid, "data": d})
            else:
                results.append({"order_id": oid, "error": r.error})
        return OperationResult.ok("track_status", results)

    def start_return(self, order_id: str) -> OperationResult:
        self._return_workflow.start(order_id)
        return OperationResult.ok("start_return", {"order_id": order_id, "state": "requested"})

    def advance_return(self, order_id: str, target: str) -> OperationResult:
        ok, msg = self._return_workflow.advance(order_id, target)
        if not ok:
            return OperationResult.fail("advance_return", msg)
        return OperationResult.ok("advance_return", {"order_id": order_id, "state": target})
