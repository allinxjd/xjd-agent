"""1688 选品模块单元测试."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from agent.ecommerce.operations.sourcing import ProductSourcer, WHOLESALE_KEYWORDS
from agent.ecommerce.platforms.alibaba1688 import Alibaba1688Platform
from agent.ecommerce.protocol import ErrorCode, OperationResult


# ── ProductSourcer Tests ──


class TestTitleRewrite:
    def setup_method(self):
        self.sourcer = ProductSourcer()

    def test_removes_wholesale_keywords(self):
        title = "厂家直销 夏季新款连衣裙 一件代发"
        result = self.sourcer._rewrite_title(title)
        assert "厂家直销" not in result
        assert "一件代发" not in result
        assert "连衣裙" in result

    def test_removes_multiple_keywords(self):
        title = "义乌批发 地摊跑量 儿童玩具 源头工厂"
        result = self.sourcer._rewrite_title(title)
        assert "义乌" not in result
        assert "批发" not in result
        assert "地摊" not in result
        assert "跑量" not in result
        assert "源头" not in result
        assert "工厂" not in result
        assert "儿童玩具" in result

    def test_adds_suffix(self):
        title = "夏季连衣裙"
        result = self.sourcer._rewrite_title(title, "包邮")
        assert result.endswith("包邮")

    def test_truncates_long_title(self):
        title = "A" * 100
        result = self.sourcer._rewrite_title(title)
        assert len(result) <= 60

    def test_cleans_extra_spaces(self):
        title = "批发  夏季   连衣裙  工厂"
        result = self.sourcer._rewrite_title(title)
        assert "  " not in result

    def test_strips_leading_trailing_punctuation(self):
        title = "，批发连衣裙，"
        result = self.sourcer._rewrite_title(title)
        assert not result.startswith("，")
        assert not result.endswith("，")

    def test_empty_title(self):
        result = self.sourcer._rewrite_title("")
        assert result == ""

    def test_oem_odm_removed(self):
        title = "OEM定制 手机壳 ODM加工"
        result = self.sourcer._rewrite_title(title)
        assert "OEM" not in result
        assert "ODM" not in result
        assert "定制" not in result
        assert "加工" not in result
        assert "手机壳" in result


class TestCalcPrice:
    def test_basic_markup(self):
        sell = ProductSourcer._calc_price(10.0, 2.0, 5.0)
        assert sell == 19.99

    def test_min_profit_guarantee(self):
        sell = ProductSourcer._calc_price(3.0, 2.0, 5.0)
        assert sell - 3.0 >= 4.99

    def test_zero_cost(self):
        sell = ProductSourcer._calc_price(0.0, 2.0, 5.0)
        assert sell == 0.0

    def test_price_ends_in_99(self):
        sell = ProductSourcer._calc_price(10.0, 2.0, 5.0)
        assert str(sell).endswith("99") or sell % 1 != 0

    def test_high_markup(self):
        sell = ProductSourcer._calc_price(50.0, 3.0, 10.0)
        assert sell >= 50.0 * 3.0 - 0.01
        assert sell - 50.0 >= 10.0

    def test_negative_cost(self):
        sell = ProductSourcer._calc_price(-5.0, 2.0, 5.0)
        assert sell == 0.0


class TestMapSkus:
    def test_basic_mapping(self):
        skus_1688 = [
            {"name": "红色 S", "price": "15.0", "stock": 100, "image": "http://img/1.jpg"},
            {"name": "蓝色 M", "price": "16.0", "stock": 50},
        ]
        result = ProductSourcer._map_skus(skus_1688, 2.0, 5.0)
        assert len(result) == 2
        assert result[0]["name"] == "红色 S"
        assert result[0]["cost_price"] == 15.0
        assert result[0]["price"] > 15.0
        assert result[0]["stock"] == 100
        assert result[0]["image"] == "http://img/1.jpg"

    def test_default_stock(self):
        skus = [{"name": "默认", "price": "10"}]
        result = ProductSourcer._map_skus(skus, 2.0, 5.0)
        assert result[0]["stock"] == 99

    def test_empty_sku_list(self):
        result = ProductSourcer._map_skus([], 2.0, 5.0)
        assert result == []


class TestGeneratePddDraft:
    def setup_method(self):
        self.sourcer = ProductSourcer()
        self.product_1688 = {
            "product_id": "123456",
            "title": "厂家直销 夏季新款连衣裙 一件代发",
            "price": 25.0,
            "images": ["http://img/1.jpg", "http://img/2.jpg"],
            "detail_images": ["http://img/d1.jpg"],
            "sku_list": [
                {"name": "红色 S", "price": "25.0", "stock": 100},
                {"name": "蓝色 M", "price": "28.0", "stock": 50},
            ],
            "category": "女装",
            "url": "https://detail.1688.com/offer/123456.html",
            "supplier_id": "b2b-001",
            "supplier_name": "测试供应商",
            "moq": 2,
        }

    def test_draft_structure(self):
        draft = self.sourcer.generate_pdd_draft(self.product_1688)
        assert "title" in draft
        assert "price" in draft
        assert "cost_price" in draft
        assert "profit" in draft
        assert "sku_list" in draft
        assert "source" in draft
        assert draft["status"] == "draft"

    def test_title_cleaned(self):
        draft = self.sourcer.generate_pdd_draft(self.product_1688)
        assert "厂家直销" not in draft["title"]
        assert "一件代发" not in draft["title"]

    def test_price_markup(self):
        draft = self.sourcer.generate_pdd_draft(self.product_1688)
        assert draft["price"] > draft["cost_price"]
        assert draft["profit"] > 0

    def test_source_metadata(self):
        draft = self.sourcer.generate_pdd_draft(self.product_1688)
        assert draft["source"]["platform"] == "1688"
        assert draft["source"]["product_id"] == "123456"
        assert draft["source"]["supplier_id"] == "b2b-001"
        assert draft["source"]["moq"] == 2

    def test_images_limited(self):
        product = {**self.product_1688, "images": [f"http://img/{i}.jpg" for i in range(20)]}
        draft = self.sourcer.generate_pdd_draft(product)
        assert len(draft["images"]) <= 10
        assert len(draft["detail_images"]) <= 15

    def test_custom_rules(self):
        rules = {"markup": 3.0, "min_profit": 10, "title_suffix": "包邮"}
        draft = self.sourcer.generate_pdd_draft(self.product_1688, rules)
        assert draft["title"].endswith("包邮")
        assert draft["price"] >= 25.0 * 3.0 - 0.01

    def test_batch_generate(self):
        products = [self.product_1688, {**self.product_1688, "product_id": "789"}]
        drafts = self.sourcer.batch_generate(products)
        assert len(drafts) == 2
        assert drafts[0]["source"]["product_id"] == "123456"
        assert drafts[1]["source"]["product_id"] == "789"


# ── Alibaba1688Platform Static Methods ──


class TestExtractJsonFromJsonp:
    def test_plain_json(self):
        text = '{"data": [1, 2, 3]}'
        result = Alibaba1688Platform._extract_json_from_jsonp(text)
        assert result == {"data": [1, 2, 3]}

    def test_jsonp_callback(self):
        text = 'callback({"data": "hello"});'
        result = Alibaba1688Platform._extract_json_from_jsonp(text)
        assert result == {"data": "hello"}

    def test_assignment_style(self):
        text = 'var data = {"key": "value"};'
        result = Alibaba1688Platform._extract_json_from_jsonp(text)
        assert result == {"key": "value"}

    def test_invalid_returns_original(self):
        text = "not json at all"
        result = Alibaba1688Platform._extract_json_from_jsonp(text)
        assert result == text


class TestParseSearchResults:
    def test_standard_offer_list(self):
        captured = [{"data": {"offerList": [
            {
                "id": "111",
                "subject": "测试商品",
                "tradePrice": "25.00",
                "quantityBegin": 2,
                "monthSold": 500,
                "company": "测试公司",
                "province": "浙江",
                "imageUrl": "http://img/1.jpg",
            },
        ]}}]
        products = Alibaba1688Platform._parse_search_results(captured)
        assert len(products) == 1
        assert products[0]["product_id"] == "111"
        assert products[0]["title"] == "测试商品"
        assert products[0]["price"] == 25.0
        assert products[0]["moq"] == 2
        assert products[0]["sold"] == 500

    def test_nested_data_path(self):
        captured = [{"data": {"data": {"offerList": [
            {"id": "222", "subject": "嵌套商品", "tradePrice": "10"},
        ]}}}]
        products = Alibaba1688Platform._parse_search_results(captured)
        assert len(products) == 1
        assert products[0]["product_id"] == "222"

    def test_price_range(self):
        captured = [{"data": {"offerList": [
            {"id": "333", "subject": "范围价", "tradePrice": "15.00-25.00"},
        ]}}]
        products = Alibaba1688Platform._parse_search_results(captured)
        assert products[0]["price"] == 15.0

    def test_empty_captured(self):
        assert Alibaba1688Platform._parse_search_results([]) == []

    def test_no_valid_offers(self):
        captured = [{"data": {"offerList": [{"no_id": True}]}}]
        products = Alibaba1688Platform._parse_search_results(captured)
        assert products == []


class TestParseDetailApi:
    def test_standard_detail(self):
        captured = [{"data": {"offerDetail": {
            "subject": "详情商品",
            "images": ["http://img/1.jpg", "http://img/2.jpg"],
            "skuInfos": [
                {"skuId": "s1", "specAttrs": "红色", "price": 20, "canBookCount": 100},
            ],
            "priceInfo": {"priceRange": [{"price": 20}]},
            "minOrderQuantity": 5,
            "memberId": "seller001",
            "companyName": "测试公司",
            "categoryName": "服装",
        }}}]
        result = Alibaba1688Platform._parse_detail_api(captured, "999")
        assert result is not None
        assert result["product_id"] == "999"
        assert result["title"] == "详情商品"
        assert result["price"] == 20.0
        assert len(result["images"]) == 2
        assert len(result["sku_list"]) == 1
        assert result["sku_list"][0]["name"] == "红色"
        assert result["supplier_id"] == "seller001"

    def test_empty_captured(self):
        assert Alibaba1688Platform._parse_detail_api([], "123") is None

    def test_no_title(self):
        captured = [{"data": {"offerDetail": {"price": 10}}}]
        assert Alibaba1688Platform._parse_detail_api(captured, "123") is None


class TestWholesaleKeywords:
    def test_matches_common_keywords(self):
        for kw in ["批发", "厂家直销", "工厂", "一件代发", "源头", "义乌", "OEM", "ODM"]:
            assert WHOLESALE_KEYWORDS.search(kw), f"Should match: {kw}"

    def test_no_false_positives(self):
        for text in ["连衣裙", "手机壳", "夏季新款"]:
            assert not WHOLESALE_KEYWORDS.search(text), f"Should not match: {text}"
