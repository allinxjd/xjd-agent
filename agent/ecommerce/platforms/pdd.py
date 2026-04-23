"""拼多多商家后台适配器 — Playwright 浏览器自动化.

操作拼多多商家管理后台 (mms.pinduoduo.com)，
通过 CDP 连接用户已登录的 Chrome 或 Playwright 内置浏览器。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from agent.ecommerce.base import EcommercePlatform
from agent.ecommerce.platforms import register_platform
from agent.ecommerce.protocol import (
    ErrorCode, OperationResult, Order, OrderStatus, Product, ShopStats,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "https://mms.pinduoduo.com/login"
HOME_URL = "https://mms.pinduoduo.com/home"
ORDER_LIST_URL = "https://mms.pinduoduo.com/orders/list"
GOODS_LIST_URL = "https://mms.pinduoduo.com/goods/goods_list"
DATA_URL = "https://mms.pinduoduo.com/sycm/overview"


@register_platform
class PddPlatform(EcommercePlatform):
    """拼多多商家后台适配器."""

    platform_name = "pdd"
    BASE_URL = "https://mms.pinduoduo.com"

    async def _get_page(self):
        if not self._session:
            from agent.ecommerce.session import BrowserSessionManager
            self._session = BrowserSessionManager()
        session = await self._session.get_session("pdd")
        return session.page

    async def _safe_goto(self, page, url: str, timeout: int = 30000) -> bool:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return True
        except Exception as e:
            logger.warning("PDD navigate failed %s: %s", url, e)
            return False

    # PLACEHOLDER_REST

    async def check_session(self) -> bool:
        try:
            page = await self._get_page()
            current = page.url or ""
            if "mms.pinduoduo.com" in current and "/login" not in current:
                return True
            if not await self._safe_goto(page, HOME_URL):
                return False
            await page.wait_for_timeout(2000)
            return "/login" not in (page.url or "")
        except Exception as e:
            logger.warning("PDD session check failed: %s", e)
            return False

    async def login(self, credentials: dict[str, Any]) -> OperationResult:
        try:
            page = await self._get_page()
            if await self.check_session():
                await self._session.save_cookies("pdd")
                return OperationResult.ok("login", {"message": "已登录拼多多商家后台"})
            if not await self._safe_goto(page, LOGIN_URL):
                return OperationResult.fail("login", "无法访问登录页", ErrorCode.NETWORK_ERROR)
            return OperationResult.ok("login", {
                "message": "已打开拼多多登录页，请在浏览器中扫码或输入账号密码登录",
                "url": LOGIN_URL,
                "status": "waiting_for_user",
            })
        except Exception as e:
            return OperationResult.fail("login", f"登录失败: {e}", ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_PRODUCTS

    async def _extract_table_data(self, page, url: str) -> tuple[list[dict], str]:
        """通用表格数据提取: 导航到页面 → 等待加载 → 提取表格."""
        if not await self._safe_goto(page, url):
            return [], "无法访问页面"
        await page.wait_for_timeout(3000)
        rows = await page.evaluate("""() => {
            const trs = document.querySelectorAll('table tbody tr, [class*="table-row"], [class*="list-item"]');
            return Array.from(trs).map(tr => {
                const cells = tr.querySelectorAll('td, [class*="cell"]');
                return Array.from(cells).map(c => c.innerText?.trim() || '');
            });
        }""")
        return rows, ""

    async def list_products(self, filters: Optional[dict[str, Any]] = None) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("list_products", "未登录", ErrorCode.AUTH_REQUIRED)
            rows, err = await self._extract_table_data(page, GOODS_LIST_URL)
            if err:
                return OperationResult.fail("list_products", err, ErrorCode.NETWORK_ERROR)
            if not rows:
                text = await page.evaluate("() => document.body?.innerText?.substring(0, 1000) || ''")
                return OperationResult.ok("list_products", {
                    "products": [], "page_preview": text[:500], "url": page.url,
                })
            products = []
            for cells in rows:
                if len(cells) >= 2:
                    products.append(Product(
                        title=cells[1] if len(cells) > 1 else "",
                        price=float("".join(c for c in (cells[2] if len(cells) > 2 else "0") if c.isdigit() or c == ".") or 0),
                        stock=int("".join(c for c in (cells[3] if len(cells) > 3 else "0") if c.isdigit()) or 0),
                        status=cells[4] if len(cells) > 4 else "",
                        platform="pdd",
                    ))
            return OperationResult.ok("list_products", products)
        except Exception as e:
            return OperationResult.fail("list_products", str(e), ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_ORDERS

    async def list_orders(self, filters: Optional[dict[str, Any]] = None) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("list_orders", "未登录", ErrorCode.AUTH_REQUIRED)
            rows, err = await self._extract_table_data(page, ORDER_LIST_URL)
            if err:
                return OperationResult.fail("list_orders", err, ErrorCode.NETWORK_ERROR)
            if not rows:
                text = await page.evaluate("() => document.body?.innerText?.substring(0, 1000) || ''")
                return OperationResult.ok("list_orders", {
                    "orders": [], "page_preview": text[:500], "url": page.url,
                })
            orders = []
            for cells in rows:
                if len(cells) >= 2:
                    orders.append(Order(
                        order_id=cells[0] if cells else "",
                        status=cells[4] if len(cells) > 4 else "",
                        total_amount=float("".join(c for c in (cells[2] if len(cells) > 2 else "0") if c.isdigit() or c == ".") or 0),
                        items=[{"description": cells[1]}] if len(cells) > 1 else [],
                        created_at=0,
                        platform="pdd",
                    ))
            return OperationResult.ok("list_orders", orders)
        except Exception as e:
            return OperationResult.fail("list_orders", str(e), ErrorCode.PLATFORM_ERROR)

    async def get_order(self, order_id: str) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("get_order", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/orders/detail?orderSn={order_id}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("get_order", "无法访问订单详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            data = await page.evaluate("() => document.body?.innerText?.substring(0, 3000) || ''")
            return OperationResult.ok("get_order", {"order_id": order_id, "detail_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_order", str(e), ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_STATS

    async def get_shop_stats(self, date_range: Optional[dict[str, Any]] = None) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("get_shop_stats", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, DATA_URL):
                return OperationResult.fail("get_shop_stats", "无法访问数据页", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            data = await page.evaluate("() => document.body?.innerText?.substring(0, 3000) || ''")
            return OperationResult.ok("get_shop_stats", {"stats_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_shop_stats", str(e), ErrorCode.PLATFORM_ERROR)

    async def get_product(self, product_id: str) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("get_product", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/goods/goods_detail?goodsId={product_id}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("get_product", "无法访问商品详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            data = await page.evaluate("() => document.body?.innerText?.substring(0, 3000) || ''")
            return OperationResult.ok("get_product", {"product_id": product_id, "detail_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_product", str(e), ErrorCode.PLATFORM_ERROR)

    async def ship_order(self, order_id: str, tracking: dict[str, Any]) -> OperationResult:
        return OperationResult.fail("ship_order", "发货操作需要在商家后台手动完成，避免误操作", ErrorCode.NOT_IMPLEMENTED)

    async def create_product(self, product: dict[str, Any]) -> OperationResult:
        return OperationResult.fail("create_product", "商品发布涉及复杂表单，建议在商家后台手动操作", ErrorCode.NOT_IMPLEMENTED)

    async def update_product(self, product_id: str, updates: dict[str, Any]) -> OperationResult:
        return OperationResult.fail("update_product", "商品编辑涉及复杂表单，建议在商家后台手动操作", ErrorCode.NOT_IMPLEMENTED)

    async def toggle_product(self, product_id: str, active: bool) -> OperationResult:
        return OperationResult.fail("toggle_product", "上下架操作建议在商家后台手动完成", ErrorCode.NOT_IMPLEMENTED)

    async def list_messages(self, filters: Optional[dict[str, Any]] = None) -> OperationResult:
        return OperationResult.fail("list_messages", "客服消息功能待实现", ErrorCode.NOT_IMPLEMENTED)

    async def reply_message(self, msg_id: str, content: str) -> OperationResult:
        return OperationResult.fail("reply_message", "客服回复功能待实现", ErrorCode.NOT_IMPLEMENTED)
