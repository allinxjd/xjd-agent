"""选品操作层 — 1688 商品 → PDD 上架草稿转换.

将 1688 采集的商品数据转换为 PDD 可上架的商品草稿，
包括标题改写、价格加价、SKU 映射、图片处理。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

WHOLESALE_KEYWORDS = re.compile(
    r"(批发|厂家直销|工厂|一件代发|源头|义乌|档口|地摊|跑量|混批|散批|"
    r"OEM|ODM|定制|加工|外贸|尾货|清仓处理|库存)",
)

DEFAULT_MARKUP = 2.0
DEFAULT_MIN_PROFIT = 5.0


class ProductSourcer:
    """1688→PDD 选品转换器."""

    def __init__(self, markup: float = DEFAULT_MARKUP, min_profit: float = DEFAULT_MIN_PROFIT):
        self.markup = markup
        self.min_profit = min_profit

    def generate_pdd_draft(self, product_1688: dict, rules: Optional[dict] = None) -> dict:
        """将 1688 商品数据转换为 PDD 上架草稿."""
        rules = rules or {}
        markup = rules.get("markup", self.markup)
        min_profit = rules.get("min_profit", self.min_profit)

        title = self._rewrite_title(product_1688.get("title", ""), rules.get("title_suffix", ""))
        cost_price = float(product_1688.get("price", 0))
        sell_price = self._calc_price(cost_price, markup, min_profit)

        sku_list = self._map_skus(product_1688.get("sku_list", []), markup, min_profit)

        images = product_1688.get("images", [])
        detail_images = product_1688.get("detail_images", [])

        return {
            "title": title,
            "price": sell_price,
            "cost_price": cost_price,
            "profit": round(sell_price - cost_price, 2),
            "images": images[:10],
            "detail_images": detail_images[:15],
            "sku_list": sku_list,
            "category": product_1688.get("category", ""),
            "source": {
                "platform": "1688",
                "product_id": product_1688.get("product_id", ""),
                "url": product_1688.get("url", ""),
                "supplier_id": product_1688.get("supplier_id", ""),
                "supplier_name": product_1688.get("supplier_name", ""),
                "moq": product_1688.get("moq", 1),
            },
            "status": "draft",
        }

    def batch_generate(self, products: list[dict], rules: Optional[dict] = None) -> list[dict]:
        """批量生成 PDD 草稿."""
        return [self.generate_pdd_draft(p, rules) for p in products]

    def _rewrite_title(self, title: str, suffix: str = "") -> str:
        """改写标题：去掉批发/工厂等词，保留商品核心描述."""
        cleaned = WHOLESALE_KEYWORDS.sub("", title)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
        cleaned = re.sub(r"^[,，、\s]+|[,，、\s]+$", "", cleaned)
        if suffix:
            cleaned = f"{cleaned} {suffix}"
        if len(cleaned) > 60:
            cleaned = cleaned[:60]
        return cleaned

    @staticmethod
    def _calc_price(cost: float, markup: float, min_profit: float) -> float:
        """计算售价：成本 × 加价倍率，保证最低利润."""
        if cost <= 0:
            return 0.0
        sell = round(cost * markup, 2)
        if sell - cost < min_profit:
            sell = round(cost + min_profit, 2)
        sell = round(sell * 100) / 100
        if sell % 1 == 0:
            sell -= 0.01
        return sell

    @staticmethod
    def _map_skus(sku_list_1688: list[dict], markup: float, min_profit: float) -> list[dict]:
        """将 1688 SKU 映射为 PDD SKU 格式."""
        pdd_skus = []
        for sku in sku_list_1688:
            cost = float(sku.get("price", 0))
            sell = ProductSourcer._calc_price(cost, markup, min_profit)
            pdd_skus.append({
                "name": sku.get("name", ""),
                "price": sell,
                "cost_price": cost,
                "stock": sku.get("stock", 99),
                "image": sku.get("image", ""),
            })
        return pdd_skus
