"""拼多多商家后台适配器 — Playwright 浏览器自动化.

操作拼多多商家管理后台 (mms.pinduoduo.com)，
通过 CDP 连接用户已登录的 Chrome 或 Playwright 内置浏览器。

写操作安全策略 (借鉴 OpenClaw dry-run):
- 简单写操作 (上下架/发货/客服回复): 直接执行
- 复杂写操作 (发布商品/编辑商品/创建活动): 填表单后返回 preview，不自动提交
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.ecommerce.base import EcommercePlatform
from agent.ecommerce.platforms import register_platform
from agent.ecommerce.protocol import (
    ErrorCode, Message, OperationResult, Order, Product,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "https://mms.pinduoduo.com/login"
HOME_URL = "https://mms.pinduoduo.com/home"
ORDER_LIST_URL = "https://mms.pinduoduo.com/orders/list"
GOODS_LIST_URL = "https://mms.pinduoduo.com/goods/goods_list"
GOODS_ADD_URL = "https://mms.pinduoduo.com/goods/add"
DATA_URL = "https://mms.pinduoduo.com/sycm/overview"
MSG_URL = "https://mms.pinduoduo.com/customer-service/im"
PROMO_URL = "https://mms.pinduoduo.com/promotion/list"
PROMO_CREATE_URL = "https://mms.pinduoduo.com/promotion/create"


@register_platform
class PddPlatform(EcommercePlatform):
    """拼多多商家后台适配器."""

    platform_name = "pdd"
    BASE_URL = "https://mms.pinduoduo.com"

    async def _get_page(self):
        if not self._session:
            from agent.ecommerce.session import get_session_manager
            self._session = get_session_manager()
        session = await self._session.get_session("pdd")
        return session.page

    # PLACEHOLDER_HELPERS

    async def _safe_goto(self, page, url: str, timeout: int = 30000) -> bool:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return True
        except Exception as e:
            logger.warning("PDD navigate failed %s: %s", url, e)
            return False

    async def _page_snapshot(self, page, max_len: int = 3000) -> str:
        """提取页面可见文本快照 (Hermes 风格)."""
        return await page.evaluate(
            f"() => document.body?.innerText?.substring(0, {max_len}) || ''"
        )

    async def _find_and_click(self, page, texts: list[str]) -> bool:
        """按文本查找并点击按钮，不依赖硬编码选择器."""
        for text in texts:
            btn = page.get_by_role("button", name=text)
            if await btn.count() > 0:
                await btn.first.click()
                return True
        for text in texts:
            el = page.locator(f'text="{text}"')
            if await el.count() > 0:
                await el.first.click()
                return True
        return False

    async def _fill_input(self, page, label: str, value: str) -> bool:
        """按 label 文本找到关联输入框并填写."""
        el = page.get_by_label(label)
        if await el.count() > 0:
            await el.first.fill(value)
            return True
        el = page.locator(f'input[placeholder*="{label}"], textarea[placeholder*="{label}"]')
        if await el.count() > 0:
            await el.first.fill(value)
            return True
        return False

    async def _upload_images(self, page, image_paths: list[str]) -> list[str]:
        """上传图片到当前页面的图片上传区域，返回成功上传的路径列表."""
        from pathlib import Path
        uploaded = []
        file_inputs = page.locator('input[type="file"][accept*="image"]')
        count = await file_inputs.count()
        if count == 0:
            file_inputs = page.locator('input[type="file"]')
            count = await file_inputs.count()
        if count == 0:
            logger.warning("PDD: 未找到文件上传输入框")
            return uploaded
        for img_path in image_paths:
            fp = Path(img_path).expanduser()
            if not fp.exists():
                logger.warning("PDD: 图片不存在: %s", fp)
                continue
            try:
                await file_inputs.first.set_input_files(str(fp))
                await page.wait_for_timeout(2000)
                uploaded.append(str(fp))
            except Exception as e:
                logger.warning("PDD: 图片上传失败 %s: %s", fp, e)
        return uploaded

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

    # PLACEHOLDER_AUTH

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
                mode = "CDP (复用已登录浏览器)" if self._session._cdp_connected else "内置 Chromium"
                return OperationResult.ok("login", {"message": f"已登录拼多多商家后台 [{mode}]"})
            if not await self._safe_goto(page, LOGIN_URL):
                return OperationResult.fail("login", "无法访问登录页", ErrorCode.NETWORK_ERROR)
            mode = "CDP (复用已登录浏览器)" if self._session._cdp_connected else "内置 Chromium"
            return OperationResult.ok("login", {
                "message": f"已打开拼多多登录页 [{mode}]，请在浏览器中扫码或输入账号密码登录",
                "url": LOGIN_URL,
                "status": "waiting_for_user",
            })
        except Exception as e:
            return OperationResult.fail("login", f"登录失败: {e}", ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_PRODUCTS

    async def list_products(self, filters: Optional[dict[str, Any]] = None) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("list_products", "未登录", ErrorCode.AUTH_REQUIRED)
            rows, err = await self._extract_table_data(page, GOODS_LIST_URL)
            if err:
                return OperationResult.fail("list_products", err, ErrorCode.NETWORK_ERROR)
            if not rows:
                text = await self._page_snapshot(page, 1000)
                return OperationResult.ok("list_products", {"products": [], "page_preview": text[:500], "url": page.url})
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

    async def get_product(self, product_id: str) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("get_product", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/goods/goods_detail?goodsId={product_id}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("get_product", "无法访问商品详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            data = await self._page_snapshot(page)
            return OperationResult.ok("get_product", {"product_id": product_id, "detail_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_product", str(e), ErrorCode.PLATFORM_ERROR)

    async def create_product(self, product: dict[str, Any]) -> OperationResult:
        """发布新商品 — preview 模式，填表单但不提交，支持图片上传."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("create_product", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, GOODS_ADD_URL):
                return OperationResult.fail("create_product", "无法访问商品发布页", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            filled = {}
            for key, label in [("title", "商品标题"), ("price", "价格"), ("stock", "库存"), ("category", "类目"), ("description", "描述")]:
                val = product.get(key, "")
                if val and await self._fill_input(page, label, str(val)):
                    filled[key] = val
            images = product.get("images", [])
            if isinstance(images, str):
                images = [images]
            uploaded = []
            if images:
                uploaded = await self._upload_images(page, images)
                if uploaded:
                    filled["images"] = uploaded
            await page.wait_for_timeout(1000)
            snapshot = await self._page_snapshot(page)
            return OperationResult.ok("create_product", {
                "status": "preview",
                "filled_fields": filled,
                "uploaded_images": uploaded,
                "page_snapshot": snapshot[:1500],
                "url": page.url,
                "instruction": "请确认信息无误后，调用 browser_action 点击提交按钮",
            })
        except Exception as e:
            return OperationResult.fail("create_product", str(e), ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_UPDATE

    async def update_product(self, product_id: str, updates: dict[str, Any]) -> OperationResult:
        """编辑商品 — preview 模式，支持图片上传."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("update_product", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/goods/goods_detail?goodsId={product_id}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("update_product", "无法访问商品详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            if not await self._find_and_click(page, ["编辑", "修改", "Edit"]):
                return OperationResult.fail("update_product", "未找到编辑按钮，请在商家后台手动编辑", ErrorCode.PLATFORM_ERROR)
            await page.wait_for_timeout(2000)
            filled = {}
            for key, label in [("title", "商品标题"), ("price", "价格"), ("stock", "库存"), ("description", "描述")]:
                val = updates.get(key, "")
                if val and await self._fill_input(page, label, str(val)):
                    filled[key] = val
            images = updates.get("images", [])
            if isinstance(images, str):
                images = [images]
            uploaded = []
            if images:
                uploaded = await self._upload_images(page, images)
                if uploaded:
                    filled["images"] = uploaded
            snapshot = await self._page_snapshot(page)
            return OperationResult.ok("update_product", {
                "status": "preview",
                "product_id": product_id,
                "updated_fields": filled,
                "uploaded_images": uploaded,
                "page_snapshot": snapshot[:1500],
                "url": page.url,
                "instruction": "请确认修改无误后，调用 browser_action 点击保存按钮",
            })
        except Exception as e:
            return OperationResult.fail("update_product", str(e), ErrorCode.PLATFORM_ERROR)

    async def toggle_product(self, product_id: str, active: bool) -> OperationResult:
        """商品上架/下架 — 直接执行."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("toggle_product", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, GOODS_LIST_URL):
                return OperationResult.fail("toggle_product", "无法访问商品列表", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            action_text = "上架" if active else "下架"
            found = await page.evaluate(f"""(productId) => {{
                const rows = document.querySelectorAll('table tbody tr, [class*="table-row"], [class*="list-item"]');
                for (const row of rows) {{
                    if (row.innerText.includes(productId)) {{
                        const btn = Array.from(row.querySelectorAll('button, a, [role="button"]'))
                            .find(b => b.innerText.includes('{action_text}'));
                        if (btn) {{ btn.click(); return true; }}
                    }}
                }}
                return false;
            }}""", product_id)
            if not found:
                if not await self._find_and_click(page, [action_text]):
                    return OperationResult.fail("toggle_product", f"未找到商品 {product_id} 的{action_text}按钮", ErrorCode.ITEM_NOT_FOUND)
            await page.wait_for_timeout(2000)
            await self._find_and_click(page, ["确定", "确认", "OK", "是"])
            await page.wait_for_timeout(2000)
            snapshot = await self._page_snapshot(page, 500)
            return OperationResult.ok("toggle_product", {
                "product_id": product_id,
                "active": active,
                "action": action_text,
                "page_snapshot": snapshot,
            })
        except Exception as e:
            return OperationResult.fail("toggle_product", str(e), ErrorCode.PLATFORM_ERROR)

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
                text = await self._page_snapshot(page, 1000)
                return OperationResult.ok("list_orders", {"orders": [], "page_preview": text[:500], "url": page.url})
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
            data = await self._page_snapshot(page)
            return OperationResult.ok("get_order", {"order_id": order_id, "detail_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_order", str(e), ErrorCode.PLATFORM_ERROR)

    async def ship_order(self, order_id: str, tracking: dict[str, Any]) -> OperationResult:
        """订单发货 — 直接执行."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("ship_order", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/orders/detail?orderSn={order_id}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("ship_order", "无法访问订单详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            if not await self._find_and_click(page, ["发货", "填写物流", "去发货"]):
                return OperationResult.fail("ship_order", "未找到发货按钮，订单可能已发货或状态不允许", ErrorCode.PLATFORM_ERROR)
            await page.wait_for_timeout(2000)
            tn = tracking.get("tracking_number", "")
            carrier = tracking.get("carrier", "")
            if tn:
                await self._fill_input(page, "物流单号", tn) or await self._fill_input(page, "运单号", tn)
            if carrier:
                await self._fill_input(page, "快递公司", carrier) or await self._fill_input(page, "物流公司", carrier)
            if not await self._find_and_click(page, ["确认发货", "提交", "确定"]):
                snapshot = await self._page_snapshot(page, 1000)
                return OperationResult.ok("ship_order", {
                    "status": "form_filled",
                    "order_id": order_id,
                    "page_snapshot": snapshot,
                    "instruction": "已填写物流信息，请手动点击确认发货按钮",
                })
            await page.wait_for_timeout(2000)
            snapshot = await self._page_snapshot(page, 500)
            return OperationResult.ok("ship_order", {
                "order_id": order_id,
                "tracking_number": tn,
                "carrier": carrier,
                "status": "shipped",
                "page_snapshot": snapshot,
            })
        except Exception as e:
            return OperationResult.fail("ship_order", str(e), ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_STATS

    async def get_shop_stats(self, date_range: Optional[dict[str, Any]] = None) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("get_shop_stats", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, DATA_URL):
                return OperationResult.fail("get_shop_stats", "无法访问数据页", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            data = await self._page_snapshot(page)
            return OperationResult.ok("get_shop_stats", {"stats_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_shop_stats", str(e), ErrorCode.PLATFORM_ERROR)

    async def get_product_stats(self, product_id: str) -> OperationResult:
        """单品数据统计."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("get_product_stats", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/goods/goods_detail?goodsId={product_id}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("get_product_stats", "无法访问商品详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            await self._find_and_click(page, ["数据", "流量", "统计", "Data"])
            await page.wait_for_timeout(2000)
            data = await self._page_snapshot(page)
            return OperationResult.ok("get_product_stats", {
                "product_id": product_id,
                "stats_text": data[:2000],
                "url": page.url,
            })
        except Exception as e:
            return OperationResult.fail("get_product_stats", str(e), ErrorCode.PLATFORM_ERROR)

    async def list_messages(self, filters: Optional[dict[str, Any]] = None) -> OperationResult:
        """客服消息列表."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("list_messages", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, MSG_URL):
                return OperationResult.fail("list_messages", "无法访问客服消息页", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            items = await page.evaluate("""() => {
                const convos = document.querySelectorAll(
                    '[class*="conversation"], [class*="chat-item"], [class*="msg-item"], [class*="session-item"]'
                );
                if (convos.length > 0) {
                    return Array.from(convos).slice(0, 20).map((el, i) => ({
                        id: String(i + 1),
                        text: el.innerText?.trim()?.substring(0, 200) || ''
                    }));
                }
                const text = document.body?.innerText?.substring(0, 2000) || '';
                return [{id: "0", text: text}];
            }""")
            messages = []
            for item in (items or []):
                text = item.get("text", "")
                lines = [l.strip() for l in text.split("\n") if l.strip()]
                messages.append(Message(
                    msg_id=item.get("id", ""),
                    sender=lines[0] if lines else "",
                    content=lines[-1] if len(lines) > 1 else text[:100],
                    platform="pdd",
                ))
            return OperationResult.ok("list_messages", messages)
        except Exception as e:
            return OperationResult.fail("list_messages", str(e), ErrorCode.PLATFORM_ERROR)

    async def reply_message(self, msg_id: str, content: str) -> OperationResult:
        """回复客服消息 — 直接执行."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("reply_message", "未登录", ErrorCode.AUTH_REQUIRED)
            current = page.url or ""
            if "customer-service" not in current and "im" not in current:
                if not await self._safe_goto(page, MSG_URL):
                    return OperationResult.fail("reply_message", "无法访问客服页面", ErrorCode.NETWORK_ERROR)
                await page.wait_for_timeout(3000)
            input_el = page.locator(
                'textarea, [contenteditable="true"], '
                'input[type="text"][class*="input"], [class*="chat-input"], [class*="reply-input"]'
            )
            if await input_el.count() == 0:
                return OperationResult.fail("reply_message", "未找到消息输入框", ErrorCode.PLATFORM_ERROR)
            await input_el.first.fill(content)
            await page.wait_for_timeout(500)
            sent = await self._find_and_click(page, ["发送", "Send"])
            if not sent:
                await page.keyboard.press("Enter")
            await page.wait_for_timeout(1000)
            return OperationResult.ok("reply_message", {"msg_id": msg_id, "content": content, "status": "sent"})
        except Exception as e:
            return OperationResult.fail("reply_message", str(e), ErrorCode.PLATFORM_ERROR)

    # PLACEHOLDER_PROMO

    async def list_promotions(self) -> OperationResult:
        """营销活动列表."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("list_promotions", "未登录", ErrorCode.AUTH_REQUIRED)
            rows, err = await self._extract_table_data(page, PROMO_URL)
            if err:
                return OperationResult.fail("list_promotions", err, ErrorCode.NETWORK_ERROR)
            if not rows:
                text = await self._page_snapshot(page, 1000)
                return OperationResult.ok("list_promotions", {"promotions": [], "page_preview": text[:500], "url": page.url})
            promos = []
            for cells in rows:
                if len(cells) >= 2:
                    promos.append({
                        "name": cells[0] if cells else "",
                        "type": cells[1] if len(cells) > 1 else "",
                        "status": cells[2] if len(cells) > 2 else "",
                        "time_range": cells[3] if len(cells) > 3 else "",
                        "platform": "pdd",
                    })
            return OperationResult.ok("list_promotions", promos)
        except Exception as e:
            return OperationResult.fail("list_promotions", str(e), ErrorCode.PLATFORM_ERROR)

    async def create_promotion(self, promo: dict[str, Any]) -> OperationResult:
        """创建营销活动 — preview 模式."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("create_promotion", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, PROMO_CREATE_URL):
                return OperationResult.fail("create_promotion", "无法访问活动创建页", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            filled = {}
            for key, label in [("name", "活动名称"), ("discount", "折扣"), ("start_date", "开始时间"), ("end_date", "结束时间")]:
                val = promo.get(key, "")
                if val and await self._fill_input(page, label, str(val)):
                    filled[key] = val
            snapshot = await self._page_snapshot(page)
            return OperationResult.ok("create_promotion", {
                "status": "preview",
                "filled_fields": filled,
                "page_snapshot": snapshot[:1500],
                "url": page.url,
                "instruction": "请确认活动信息无误后，调用 browser_action 点击提交按钮",
            })
        except Exception as e:
            return OperationResult.fail("create_promotion", str(e), ErrorCode.PLATFORM_ERROR)
