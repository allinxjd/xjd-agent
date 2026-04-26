"""数据分析操作 — 流量/转化/竞品分析.

平台适配器实现具体的数据抓取，本模块提供跨平台的通用逻辑:
- 数据聚合与对比
- 趋势分析
- 竞品监控
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from agent.ecommerce.protocol import OperationResult, ShopStats

logger = logging.getLogger(__name__)

_CN_NUM_RE = re.compile(r"([\d,]+\.?\d*)")
_METRIC_PATTERNS: dict[str, list[re.Pattern]] = {
    "views": [re.compile(r"(?:浏览[量次数]|访问量|PV)[^\d]{0,5}([\d,]+)")],
    "visitors": [re.compile(r"(?:访客[数量]?|UV)[^\d]{0,5}([\d,]+)")],
    "orders": [re.compile(r"(?:订单[数量]?|成交[笔单])[^\d]{0,5}([\d,]+)")],
    "revenue": [re.compile(r"(?:营业额|销售额|GMV|成交额)[^\d]{0,5}([\d,.]+)")],
    "conversion_rate": [re.compile(r"(?:转化率|支付转化)[^\d]{0,5}([\d.]+)%?")],
}


class DataParser:
    """从页面文本快照中提取结构化数据."""

    @staticmethod
    def parse_number(text: str) -> Optional[float]:
        m = _CN_NUM_RE.search(text)
        if m:
            return float(m.group(1).replace(",", ""))
        return None

    @classmethod
    def parse_shop_stats(cls, text: str, platform: str = "") -> ShopStats:
        stats = ShopStats(platform=platform)
        for metric, patterns in _METRIC_PATTERNS.items():
            for pat in patterns:
                m = pat.search(text)
                if m:
                    val = m.group(1).replace(",", "")
                    try:
                        if metric == "conversion_rate":
                            setattr(stats, metric, float(val) / 100.0)
                        elif metric == "revenue":
                            setattr(stats, metric, float(val))
                        else:
                            setattr(stats, metric, int(float(val)))
                    except (ValueError, TypeError):
                        pass
                    break
        return stats


class AnalyticsAggregator:
    """跨平台数据分析编排."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform
        self._history: list[ShopStats] = []

    async def daily_summary(self) -> OperationResult:
        r = await self._platform.get_shop_stats({})
        if not r.success:
            return r
        text = r.data if isinstance(r.data, str) else str(r.data)
        stats = DataParser.parse_shop_stats(text, self._platform.platform_name)
        self._history.append(stats)
        return OperationResult.ok("daily_summary", stats.to_dict())

    def trend_analysis(self, days: int = 7) -> OperationResult:
        recent = self._history[-days:] if len(self._history) >= days else self._history
        if len(recent) < 2:
            return OperationResult.ok("trend_analysis", {"message": "数据不足，至少需要 2 天"})
        first, last = recent[0], recent[-1]
        def delta(a: float, b: float) -> Optional[float]:
            if a == 0:
                return None
            return round((b - a) / a * 100, 1)
        return OperationResult.ok("trend_analysis", {
            "period_days": len(recent),
            "views_change": delta(first.views, last.views),
            "visitors_change": delta(first.visitors, last.visitors),
            "orders_change": delta(first.orders, last.orders),
            "revenue_change": delta(first.revenue, last.revenue),
        })

    async def product_ranking(self, metric: str = "sales", limit: int = 10) -> OperationResult:
        r = await self._platform.list_products({})
        if not r.success:
            return r
        products = r.data if isinstance(r.data, list) else []
        def sort_key(p: Any) -> float:
            d = p.to_dict() if hasattr(p, "to_dict") else (p if isinstance(p, dict) else {})
            meta = d.get("metadata", {})
            if metric == "sales":
                return float(meta.get("sales", 0))
            elif metric == "views":
                return float(meta.get("views", 0))
            return float(meta.get("sales", 0))
        products.sort(key=sort_key, reverse=True)
        ranked = products[:limit]
        return OperationResult.ok("product_ranking", [
            p.to_dict() if hasattr(p, "to_dict") else p for p in ranked
        ])

    async def generate_report(self, period: str = "daily") -> OperationResult:
        summary = await self.daily_summary()
        if not summary.success:
            return summary
        d = summary.data
        lines = [
            f"## 店铺{period}报告",
            f"- 浏览量: {d.get('views', '—')}",
            f"- 访客数: {d.get('visitors', '—')}",
            f"- 订单数: {d.get('orders', '—')}",
            f"- 营业额: ¥{d.get('revenue', '—')}",
            f"- 转化率: {round(d.get('conversion_rate', 0) * 100, 1)}%",
        ]
        trend = self.trend_analysis()
        if trend.success and trend.data.get("period_days", 0) >= 2:
            t = trend.data
            lines.append("\n### 趋势")
            for k in ("views_change", "orders_change", "revenue_change"):
                v = t.get(k)
                if v is not None:
                    label = k.replace("_change", "")
                    sign = "+" if v > 0 else ""
                    lines.append(f"- {label}: {sign}{v}%")
        return OperationResult.ok("generate_report", "\n".join(lines))
