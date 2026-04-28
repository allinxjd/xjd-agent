"""营销推广操作 — 活动/优惠券/广告投放.

平台适配器实现具体的页面操作，本模块提供跨平台的通用逻辑:
- 活动模板
- 优惠券批量创建
- 广告投放策略
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.ecommerce.protocol import ErrorCode, OperationResult

logger = logging.getLogger(__name__)


class PromotionManager:
    """跨平台营销活动编排."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def create_coupon(self, config: dict[str, Any]) -> OperationResult:
        required = {"discount_type", "discount_value"}
        missing = required - set(config.keys())
        if missing:
            return OperationResult.fail(
                "create_coupon",
                f"缺少必填字段: {', '.join(missing)}",
                ErrorCode.INVALID_PARAMS,
            )
        promo_data = {
            "type": "coupon",
            "discount_type": config["discount_type"],
            "discount_value": config["discount_value"],
            "min_spend": config.get("min_spend", 0),
            "quantity": config.get("quantity", 100),
            "start_date": config.get("start_date", ""),
            "end_date": config.get("end_date", ""),
        }
        return await self._platform.create_promotion(promo_data)

    async def list_active(self) -> OperationResult:
        r = await self._platform.list_promotions()
        if not r.success:
            return r
        promos = r.data if isinstance(r.data, list) else []
        active = [
            p for p in promos
            if (p.get("status") if isinstance(p, dict) else "") in ("active", "进行中", "已开始")
        ]
        return OperationResult.ok("list_active", active)

    @staticmethod
    def estimate_roi(
        campaign_data: dict[str, Any],
        historical_stats: dict[str, Any],
    ) -> dict[str, Any]:
        budget = float(campaign_data.get("budget", 0))
        hist_conv = float(historical_stats.get("conversion_rate", 0.02))
        hist_aov = float(historical_stats.get("avg_order_value", 50))
        conv_is_default = "conversion_rate" not in historical_stats
        aov_is_default = "avg_order_value" not in historical_stats
        cpc = float(campaign_data.get("cpc", 1.0))
        if cpc <= 0:
            return {"error": "CPC 必须大于 0"}
        est_clicks = budget / cpc
        est_orders = est_clicks * hist_conv
        est_revenue = est_orders * hist_aov
        est_roi = (est_revenue - budget) / budget if budget > 0 else 0
        defaults_used = []
        if conv_is_default:
            defaults_used.append("conversion_rate=0.02")
        if aov_is_default:
            defaults_used.append("avg_order_value=50")
        result = {
            "budget": budget,
            "estimated_clicks": round(est_clicks),
            "estimated_orders": round(est_orders, 1),
            "estimated_revenue": round(est_revenue, 2),
            "estimated_roi": round(est_roi * 100, 1),
            "note": "基于历史转化率预估，实际效果受多种因素影响",
        }
        if defaults_used:
            result["defaults_used"] = defaults_used
            result["note"] += f"（以下参数使用默认值: {', '.join(defaults_used)}，建议提供真实数据）"
        return result


class AdCampaignManager:
    """广告投放管理（Playwright 浏览器自动化）."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    async def create_campaign(self, config: dict[str, Any]) -> OperationResult:
        required = {"campaign_name", "budget", "bid"}
        missing = required - set(config.keys())
        if missing:
            return OperationResult.fail(
                "create_ad",
                f"缺少必填字段: {', '.join(missing)}",
                ErrorCode.INVALID_PARAMS,
            )
        if not hasattr(self._platform, "create_ad_campaign"):
            return OperationResult.fail("create_ad", "当前平台不支持广告管理", ErrorCode.NOT_IMPLEMENTED)
        return await self._platform.create_ad_campaign(config)

    async def pause_campaign(self, campaign_id: str) -> OperationResult:
        if not hasattr(self._platform, "pause_ad_campaign"):
            return OperationResult.fail("pause_ad", "当前平台不支持广告管理", ErrorCode.NOT_IMPLEMENTED)
        return await self._platform.pause_ad_campaign(campaign_id)

    async def resume_campaign(self, campaign_id: str) -> OperationResult:
        if not hasattr(self._platform, "resume_ad_campaign"):
            return OperationResult.fail("resume_ad", "当前平台不支持广告管理", ErrorCode.NOT_IMPLEMENTED)
        return await self._platform.resume_ad_campaign(campaign_id)

    async def adjust_budget(self, campaign_id: str, new_budget: float) -> OperationResult:
        if not hasattr(self._platform, "adjust_ad_budget"):
            return OperationResult.fail("adjust_budget", "当前平台不支持广告管理", ErrorCode.NOT_IMPLEMENTED)
        return await self._platform.adjust_ad_budget(campaign_id, new_budget)

    async def get_stats(self, campaign_id: str) -> OperationResult:
        if not hasattr(self._platform, "get_ad_stats"):
            return OperationResult.fail("ad_stats", "当前平台不支持广告管理", ErrorCode.NOT_IMPLEMENTED)
        return await self._platform.get_ad_stats(campaign_id)
