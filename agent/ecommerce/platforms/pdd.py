"""拼多多商家后台适配器 — Playwright 浏览器自动化.

操作拼多多商家管理后台 (mms.pinduoduo.com)，
通过 CDP 连接用户已登录的 Chrome 或 Playwright 内置浏览器。

写操作安全策略:
- 简单写操作 (上下架/发货/客服回复): 直接执行
- 复杂写操作 (发布商品/编辑商品/创建活动): 填表单后返回 preview，不自动提交
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any, Optional
from urllib.parse import quote as _url_quote

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
MSG_URL = "https://mms.pinduoduo.com/chat-merchant/index.html"
PROMO_URL = "https://mms.pinduoduo.com/promotion/list"
PROMO_CREATE_URL = "https://mms.pinduoduo.com/promotion/create"
AD_LIST_URL = "https://mms.pinduoduo.com/ad/list"
AD_CREATE_URL = "https://mms.pinduoduo.com/ad/create"
AD_DETAIL_URL = "https://mms.pinduoduo.com/ad/detail"


@register_platform
class PddPlatform(EcommercePlatform):
    """拼多多商家后台适配器."""

    platform_name = "pdd"
    BASE_URL = "https://mms.pinduoduo.com"
    _nav_lock = asyncio.Lock()

    async def _get_page(self):
        if not self._session:
            from agent.ecommerce.session import get_session_manager
            self._session = get_session_manager()
        session = await self._session.get_session("pdd")
        return session.page


    async def _safe_goto(self, page, url: str, timeout: int = 30000) -> bool:
        """导航到指定 URL，自动检测登录页重定向."""
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            final_url = page.url or ""
            if "/login" in final_url and "/login" not in url:
                logger.warning("PDD session expired — redirected to login")
                return False
            return True
        except Exception as e:
            logger.warning("PDD navigate failed %s: %s", url, e)
            return False

    async def _page_snapshot(self, page, max_len: int = 3000) -> str:
        """提取页面可见文本快照."""
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

    async def _get_goods_list(self, page, _retry: int = 0) -> list[dict]:
        """获取商品列表 — 被动监听页面自身 API 请求的响应."""
        captured = []

        async def _on_response(response):
            try:
                if "goodsList" in response.url and response.status == 200:
                    body = await response.json()
                    if isinstance(body, dict):
                        captured.append(body)
            except Exception:
                pass

        page.on("response", _on_response)
        try:
            # about:blank 强制打断 SPA 路由缓存，确保重新发起 API 请求
            await page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
            await page.wait_for_timeout(1000)
            await self._safe_goto(page, f"{GOODS_LIST_URL}?searchType=0&status=-2")
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(3000)
        finally:
            page.remove_listener("response", _on_response)

        for body in captured:
            err_code = body.get("error_code")
            if err_code == 54001 and _retry < 2:
                logger.warning("PDD rate limited (54001), retry %d", _retry + 1)
                await page.wait_for_timeout(5000)
                return await self._get_goods_list(page, _retry + 1)
            result_val = body.get("result")
            # result 直接是商品列表
            if isinstance(result_val, list) and result_val:
                if isinstance(result_val[0], dict):
                    return result_val
            # result 是 dict，在其中查找
            if isinstance(result_val, dict):
                for key in ("goodsList", "goods_list", "goodsInfoList", "list"):
                    val = result_val.get(key)
                    if isinstance(val, list) and val:
                        return val
        return []

    async def _extract_table_data(self, page, url: str) -> tuple[list[dict], str]:
        """通用表格数据提取: 优先直接 API 调用，回退 DOM 解析."""
        # ── 策略0: 直接调用 PDD 内部 API ──
        if "goods" in url:
            rows = await self._get_goods_list(page)
            if rows:
                return rows, ""

        # ── 回退: 导航 + DOM 解析 ──
        if not await self._safe_goto(page, url):
            return [], "无法访问页面"
        await page.wait_for_timeout(3000)
        data = await page.evaluate("""() => {
            const table = document.querySelector('table');
            if (table) {
                const ths = table.querySelectorAll('thead th, thead td');
                const headers = Array.from(ths).map(h => h.innerText?.trim() || '');
                const trs = table.querySelectorAll('tbody tr');
                const rows = Array.from(trs).map(tr => {
                    const cells = tr.querySelectorAll('td, [class*="cell"]');
                    return Array.from(cells).map(c => c.innerText?.trim() || '');
                });
                if (rows.length > 0) return {headers, rows};
            }
            const antTable = document.querySelector('.ant-table-tbody');
            if (antTable) {
                const hdrEls = document.querySelectorAll('.ant-table-thead th');
                const headers = Array.from(hdrEls).map(h => h.innerText?.trim() || '');
                const trs = antTable.querySelectorAll('tr');
                const rows = Array.from(trs).map(tr => {
                    const cells = tr.querySelectorAll('td');
                    return Array.from(cells).map(c => c.innerText?.trim() || '');
                }).filter(r => r.length > 0);
                if (rows.length > 0) return {headers, rows};
            }
            return {headers: [], rows: []};
        }""")
        headers = data.get("headers", []) if isinstance(data, dict) else []
        raw_rows = data.get("rows", []) if isinstance(data, dict) else data
        if not raw_rows:
            return [], ""
        if raw_rows and isinstance(raw_rows[0], dict):
            return raw_rows, ""
        if headers:
            return [dict(zip(headers, row)) for row in raw_rows], ""
        return raw_rows, ""



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

    async def login(self, credentials: dict[str, Any], force_new: bool = False) -> OperationResult:
        try:
            page = await self._get_page()
            if not force_new and await self.check_session():
                info = await self._detect_shop_info()
                mall_id = info.get("mall_id", "")
                mall_name = info.get("mall_name", "")
                if mall_id:
                    await self._session.save_cookies("pdd", mall_id)
                else:
                    await self._session.save_cookies("pdd")
                mode = "CDP (复用已登录浏览器)" if self._session._cdp_connected else "内置 Chromium"
                return OperationResult.ok("login", {
                    "message": f"已登录拼多多商家后台 [{mode}]",
                    "mall_id": mall_id,
                    "mall_name": mall_name,
                })

            # 尝试从已保存的 cookie 恢复登录态
            if not force_new:
                restored = await self._try_restore_cookies(page)
                if restored:
                    info = await self._detect_shop_info()
                    mall_id = info.get("mall_id", "")
                    mall_name = info.get("mall_name", "")
                    mode = "Cookie 恢复"
                    return OperationResult.ok("login", {
                        "message": f"已登录拼多多商家后台 [{mode}]",
                        "mall_id": mall_id,
                        "mall_name": mall_name,
                    })

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

    async def _try_restore_cookies(self, page) -> bool:
        """尝试从已保存的 cookie 文件恢复登录态."""
        try:
            accounts = self._session.list_accounts("pdd")
            if not accounts:
                return False
            for acc in sorted(
                accounts,
                key=lambda a: (self._session._data_dir / "pdd" / a / "cookies.json").stat().st_mtime,
                reverse=True,
            ):
                saved = self._session.load_cookies("pdd", acc)
                if not saved:
                    continue
                pdd_cookies = [c for c in saved if "pinduoduo" in c.get("domain", "")]
                if not pdd_cookies:
                    continue
                if page.is_closed():
                    pages = self._session._context.pages
                    page = pages[0] if pages else await self._session._context.new_page()
                await self._session._context.add_cookies(pdd_cookies)
                logger.info("尝试恢复 cookie: pdd/%s (%d cookies)", acc, len(pdd_cookies))
                try:
                    if not await self._safe_goto(page, HOME_URL):
                        continue
                    await page.wait_for_timeout(2000)
                except Exception:
                    logger.info("Cookie 恢复导航失败: pdd/%s", acc)
                    continue
                if "/login" not in (page.url or ""):
                    logger.info("Cookie 恢复成功: pdd/%s", acc)
                    await self._session.save_cookies("pdd", acc)
                    return True
                logger.info("Cookie 已过期: pdd/%s", acc)
            return False
        except Exception as e:
            logger.warning("Cookie 恢复失败: %s", e)
            return False

    async def _detect_shop_info(self) -> dict[str, str]:
        """登录后检测店铺 mall_id 和 mall_name."""
        info: dict[str, str] = {}
        try:
            import aiohttp
            session = await self._session.get_session("pdd")
            raw_cookies = await session.context.cookies()
            cookie_dict = {c["name"]: c["value"] for c in raw_cookies if "pinduoduo" in c.get("domain", "")}
            if not cookie_dict:
                return info
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookie_dict.items())
            headers = {"Cookie": cookie_str}
            async with aiohttp.ClientSession() as http:
                async with http.post(
                    "https://mms.pinduoduo.com/chats/getToken",
                    headers=headers, json={"version": "3"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    data = await resp.json()
                    mall_id = data.get("mall_id") or data.get("result", {}).get("mall_id")
                    if mall_id:
                        info["mall_id"] = str(mall_id)
                        logger.info("PDD 检测到店铺 mall_id=%s", mall_id)
                    nickname = data.get("nickname") or data.get("result", {}).get("nickname")
                    if nickname and nickname != "主账号":
                        info["mall_name"] = nickname
                # 获取店铺名称 (API 方式)
                async with http.get(
                    "https://mms.pinduoduo.com/sydney/api/shop/info",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp2:
                    data2 = await resp2.json()
                    name = (
                        data2.get("result", {}).get("mall_name")
                        or data2.get("mall_name")
                        or data2.get("result", {}).get("mallName")
                        or ""
                    )
                    if name:
                        info["mall_name"] = name
                        logger.info("PDD 店铺名称=%s", name)
            # fallback: 从页面 DOM 提取店铺名
            if not info.get("mall_name"):
                try:
                    page = await self._get_page()
                    name_el = await page.query_selector(".user-name-name")
                    if name_el:
                        dom_name = (await name_el.inner_text()).strip()
                        if dom_name:
                            info["mall_name"] = dom_name
                            logger.info("PDD 店铺名称(DOM)=%s", dom_name)
                except Exception:
                    pass
            if info.get("mall_id"):
                if not info.get("mall_name"):
                    logger.warning("PDD 店铺 %s 未能获取名称（getToken/API/DOM 均失败）", info["mall_id"])
                shop_dir = self._session._data_dir / "pdd" / info["mall_id"]
                shop_dir.mkdir(parents=True, exist_ok=True)
                (shop_dir / "shop_info.json").write_text(
                    _json.dumps(info, ensure_ascii=False, indent=2),
                )
        except Exception as e:
            logger.warning("PDD 检测店铺信息失败: %s", e)
        return info



    async def list_products(self, filters: Optional[dict[str, Any]] = None) -> OperationResult:
        try:
            logger.debug("PDD list_products called, filters=%s", filters)
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("list_products", "未登录", ErrorCode.AUTH_REQUIRED)
            # 默认查全部商品（status=-2），避免只看"在售"漏掉已下架的
            status_filter = (filters or {}).get("status", "-2")
            url = f"{GOODS_LIST_URL}?searchType=0&status={status_filter}"
            rows, err = await self._extract_table_data(page, url)
            if err:
                return OperationResult.fail("list_products", err, ErrorCode.NETWORK_ERROR)
            if not rows:
                text = await self._page_snapshot(page, 1000)
                return OperationResult.ok("list_products", {"products": [], "page_preview": text[:500], "url": page.url})
            products = []
            for row in rows:
                if isinstance(row, dict):
                    if "raw" in row and "商品名称" not in row:
                        continue
                    # API 拦截返回的原始字段 (camelCase / snake_case)
                    title = (
                        row.get("goodsName") or row.get("goods_name")
                        or row.get("商品名称") or row.get("商品标题")
                        or row.get("title") or row.get("name") or ""
                    )
                    price_raw = row.get("sku_group_price")
                    if price_raw is None:
                        price_raw = (
                            row.get("sku_price") or row.get("minGroupPrice")
                            or row.get("min_group_price") or row.get("goodsPrice")
                            or row.get("goods_price") or row.get("价格")
                            or row.get("团购价") or row.get("price") or 0
                        )
                    # PDD 价格字段可能是数组 [min, max]，取第一个
                    if isinstance(price_raw, list) and price_raw:
                        price_raw = price_raw[0]
                    # PDD 价格单位是厘（1元=1000厘），转为元
                    if isinstance(price_raw, (int, float)) and price_raw > 100:
                        price_raw = price_raw / 1000
                    stock_raw = (
                        row.get("quantity")
                        or row.get("goodsQuantity") or row.get("goods_quantity")
                        or row.get("totalQuantity") or row.get("库存")
                        or row.get("stock") or "0"
                    )
                    status_val = (
                        row.get("is_onsale") or row.get("isOnsale")
                        or row.get("goodsStatus") or row.get("goods_status")
                        or row.get("状态") or row.get("status") or ""
                    )
                    # 转换 isOnsale 数值为可读状态
                    if status_val in (1, "1", True):
                        status_val = "在售"
                    elif status_val in (0, "0", False):
                        status_val = "已下架"
                    product_id = str(
                        row.get("goodsId") or row.get("goods_id")
                        or row.get("id") or ""
                    )
                else:
                    cells = row
                    title = cells[1] if len(cells) > 1 else ""
                    price_raw = cells[2] if len(cells) > 2 else "0"
                    stock_raw = cells[3] if len(cells) > 3 else "0"
                    status_val = cells[4] if len(cells) > 4 else ""
                    product_id = ""
                if not title:
                    continue
                products.append(Product(
                    product_id=product_id if isinstance(product_id, str) else str(product_id),
                    title=str(title),
                    price=float("".join(c for c in str(price_raw) if c.isdigit() or c == ".") or 0),
                    stock=int("".join(c for c in str(stock_raw) if c.isdigit()) or 0),
                    status=str(status_val),
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
            rows = await self._get_goods_list(page)
            target = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("id") or row.get("goods_id") or row.get("goodsId") or "")
                if rid == str(product_id):
                    target = row
                    break
            if target:
                return OperationResult.ok("get_product", {
                    "product_id": product_id, "detail": target,
                })
            data = await self._page_snapshot(page)
            return OperationResult.ok("get_product", {
                "product_id": product_id, "detail_text": data[:2000], "url": page.url,
            })
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



    async def update_product(self, product_id: str, updates: dict[str, Any]) -> OperationResult:
        """编辑商品 — preview 模式，支持图片上传."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("update_product", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/goods/goods_edit?goodsId={_url_quote(str(product_id))}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("update_product", "无法访问商品编辑页", ErrorCode.NETWORK_ERROR)
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
        """商品上架/下架 — 通过商品 ID 精确定位，操作后验证结果."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("toggle_product", "未登录", ErrorCode.AUTH_REQUIRED)

            # 1. 确认商品存在并获取当前状态
            rows = await self._get_goods_list(page)
            target = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("id") or row.get("goods_id") or row.get("goodsId") or "")
                if rid == str(product_id):
                    target = row
                    break
            if not target:
                return OperationResult.fail("toggle_product", f"未找到商品 {product_id}", ErrorCode.ITEM_NOT_FOUND)

            current_onsale = target.get("is_onsale")
            if (active and current_onsale in (True, 1, "1")) or (not active and current_onsale in (False, 0, "0")):
                status_text = "在售" if active else "已下架"
                return OperationResult.ok("toggle_product", {
                    "product_id": product_id, "active": active,
                    "message": f"商品已经是{status_text}状态，无需操作",
                })

            action_text = "上架" if active else "下架"

            # 2. 自动处理确认对话框
            async def _handle_dialog(dialog):
                await dialog.accept()
            page.on("dialog", _handle_dialog)

            try:
                # 3. 通过商品 ID 精确定位操作按钮（链接 href 或 data 属性包含 goods ID）
                clicked = await page.evaluate("""({productId, actionText}) => {
                    const links = document.querySelectorAll('a[href*="' + productId + '"]');
                    for (const link of links) {
                        const row = link.closest('tr, [class*="row"], [class*="item"], [class*="card"]');
                        if (!row) continue;
                        const btns = row.querySelectorAll('button, a, span[class*="btn"], [role="button"]');
                        for (const btn of btns) {
                            if (btn.innerText && btn.innerText.trim().includes(actionText)) {
                                btn.click();
                                return 'clicked';
                            }
                        }
                    }
                    // 回退：遍历所有行，用 innerText 包含商品 ID 定位
                    const rows = document.querySelectorAll('tr, [class*="goods-item"], [class*="list-item"]');
                    for (const row of rows) {
                        const text = row.innerText || '';
                        if (text.includes(productId)) {
                            const btns = row.querySelectorAll('button, a, span[class*="btn"], [role="button"]');
                            for (const btn of btns) {
                                if (btn.innerText && btn.innerText.trim().includes(actionText)) {
                                    btn.click();
                                    return 'clicked_fallback';
                                }
                            }
                        }
                    }
                    return null;
                }""", {"productId": str(product_id), "actionText": action_text})

                if not clicked:
                    return OperationResult.fail("toggle_product", f"未找到商品 {product_id} 的{action_text}按钮", ErrorCode.ITEM_NOT_FOUND)

                await page.wait_for_timeout(2000)
                await self._find_and_click(page, ["确定", "确认", "OK", "是"])
                await page.wait_for_timeout(3000)

                # 4. 验证操作结果
                rows_after = await self._get_goods_list(page)
                for row in rows_after:
                    if not isinstance(row, dict):
                        continue
                    rid = str(row.get("id") or row.get("goods_id") or "")
                    if rid == str(product_id):
                        new_onsale = row.get("is_onsale")
                        if (active and new_onsale in (True, 1, "1")) or (not active and new_onsale in (False, 0, "0")):
                            return OperationResult.ok("toggle_product", {
                                "product_id": product_id, "active": active,
                                "action": action_text, "verified": True,
                            })
                        break

                snapshot = await self._page_snapshot(page, 500)
                return OperationResult.ok("toggle_product", {
                    "product_id": product_id, "active": active,
                    "action": action_text, "verified": False,
                    "page_snapshot": snapshot,
                })
            finally:
                page.remove_listener("dialog", _handle_dialog)
        except Exception as e:
            return OperationResult.fail("toggle_product", str(e), ErrorCode.PLATFORM_ERROR)



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
            for row in rows:
                if isinstance(row, dict):
                    oid = row.get("订单号", row.get("订单编号", row.get("order_id", "")))
                    desc = row.get("商品", row.get("商品信息", row.get("商品名称", "")))
                    amount_raw = row.get("金额", row.get("订单金额", row.get("实付", "0")))
                    status_val = row.get("状态", row.get("订单状态", row.get("status", "")))
                else:
                    cells = row
                    oid = cells[0] if cells else ""
                    desc = cells[1] if len(cells) > 1 else ""
                    amount_raw = cells[2] if len(cells) > 2 else "0"
                    status_val = cells[4] if len(cells) > 4 else ""
                orders.append(Order(
                    order_id=str(oid),
                    status=str(status_val),
                    total_amount=float("".join(c for c in str(amount_raw) if c.isdigit() or c == ".") or 0),
                    items=[{"description": str(desc)}] if desc else [],
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
            url = f"{self.BASE_URL}/orders/detail?orderSn={_url_quote(str(order_id))}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("get_order", "无法访问订单详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)
            data = await self._page_snapshot(page)
            return OperationResult.ok("get_order", {"order_id": order_id, "detail_text": data[:2000], "url": page.url})
        except Exception as e:
            return OperationResult.fail("get_order", str(e), ErrorCode.PLATFORM_ERROR)

    async def ship_order(self, order_id: str, tracking: dict[str, Any]) -> OperationResult:
        """订单发货 — 填写物流信息并提交，验证操作结果."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("ship_order", "未登录", ErrorCode.AUTH_REQUIRED)
            url = f"{self.BASE_URL}/orders/detail?orderSn={_url_quote(str(order_id))}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("ship_order", "无法访问订单详情", ErrorCode.NETWORK_ERROR)
            await page.wait_for_timeout(3000)

            # 自动处理确认对话框
            async def _handle_dialog(dialog):
                await dialog.accept()
            page.on("dialog", _handle_dialog)

            try:
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
                await page.wait_for_timeout(3000)

                # 验证操作结果：检查页面是否出现成功提示或状态变更
                snapshot = await self._page_snapshot(page, 1000)
                success_keywords = ["发货成功", "已发货", "物流信息已提交", "操作成功"]
                verified = any(kw in snapshot for kw in success_keywords)
                return OperationResult.ok("ship_order", {
                    "order_id": order_id,
                    "tracking_number": tn,
                    "carrier": carrier,
                    "status": "shipped" if verified else "submitted",
                    "verified": verified,
                    "page_snapshot": snapshot[:500],
                })
            finally:
                page.remove_listener("dialog", _handle_dialog)
        except Exception as e:
            return OperationResult.fail("ship_order", str(e), ErrorCode.PLATFORM_ERROR)



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
            rows = await self._get_goods_list(page)
            target = None
            for row in rows:
                if not isinstance(row, dict):
                    continue
                rid = str(row.get("id") or row.get("goods_id") or "")
                if rid == str(product_id):
                    target = row
                    break
            # 尝试数据中心页面获取统计
            stats_url = f"{self.BASE_URL}/sycm/goods_detail?goodsId={_url_quote(str(product_id))}"
            if not await self._safe_goto(page, stats_url):
                pass
            await page.wait_for_timeout(3000)
            data = await self._page_snapshot(page)
            result = {"product_id": product_id, "stats_text": data[:2000], "url": page.url}
            if target:
                result["product_info"] = target
            return OperationResult.ok("get_product_stats", result)
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
        """回复客服消息 — 通过 Playwright locator 定位输入框，验证发送结果."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("reply_message", "未登录", ErrorCode.AUTH_REQUIRED)
            current = page.url or ""
            if "chat-merchant" not in current:
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
            await page.wait_for_timeout(1500)
            # 验证：检查输入框是否已清空（发送成功后通常会清空）
            try:
                remaining = await input_el.first.input_value()
                verified = not remaining or remaining != content
            except Exception:
                verified = True
            return OperationResult.ok("reply_message", {
                "msg_id": msg_id, "content": content,
                "status": "sent", "verified": verified,
            })
        except Exception as e:
            return OperationResult.fail("reply_message", str(e), ErrorCode.PLATFORM_ERROR)



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

    # ── 广告投放 ──

    async def create_ad_campaign(self, config: dict[str, Any]) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail("create_ad", "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, AD_CREATE_URL):
                return OperationResult.fail("create_ad", "无法打开广告创建页", ErrorCode.PLATFORM_ERROR)
            await page.wait_for_load_state("networkidle", timeout=10000)
            filled = []
            for key in ("campaign_name", "budget", "bid"):
                val = config.get(key)
                if val and await self._fill_input(page, key, str(val)):
                    filled.append(key)
            snapshot = await self._page_snapshot(page)
            return OperationResult.ok("create_ad", {
                "status": "preview",
                "filled_fields": filled,
                "page_snapshot": snapshot[:1500],
                "url": page.url,
                "instruction": "请确认广告信息无误后，调用 browser_action 点击提交按钮",
            })
        except Exception as e:
            return OperationResult.fail("create_ad", str(e), ErrorCode.PLATFORM_ERROR)

    async def pause_ad_campaign(self, campaign_id: str) -> OperationResult:
        return await self._toggle_ad(campaign_id, pause=True)

    async def resume_ad_campaign(self, campaign_id: str) -> OperationResult:
        return await self._toggle_ad(campaign_id, pause=False)

    async def _toggle_ad(self, campaign_id: str, pause: bool) -> OperationResult:
        action = "pause_ad" if pause else "resume_ad"
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail(action, "未登录", ErrorCode.AUTH_REQUIRED)
            if not await self._safe_goto(page, AD_LIST_URL):
                return OperationResult.fail(action, "无法打开广告列表", ErrorCode.PLATFORM_ERROR)
            await page.wait_for_load_state("networkidle", timeout=10000)
            row = page.locator(f'text="{campaign_id}"').first
            if await row.count() == 0:
                return OperationResult.fail(action, f"未找到广告 {campaign_id}", ErrorCode.ITEM_NOT_FOUND)

            async def _handle_dialog(dialog):
                await dialog.accept()
            page.on("dialog", _handle_dialog)

            try:
                btn_text = ["暂停", "pause"] if pause else ["恢复", "启动", "resume"]
                parent = row.locator("xpath=ancestor::tr")
                clicked = False
                for t in btn_text:
                    btn = parent.get_by_role("button", name=t)
                    if await btn.count() > 0:
                        await btn.first.click()
                        clicked = True
                        break
                if not clicked:
                    return OperationResult.fail(action, "未找到操作按钮", ErrorCode.PLATFORM_ERROR)
                await page.wait_for_timeout(2000)
                await self._find_and_click(page, ["确定", "确认", "OK"])
                await page.wait_for_timeout(2000)
                snapshot = await self._page_snapshot(page, 500)
                action_text = "暂停" if pause else "恢复"
                verified = any(kw in snapshot for kw in [f"已{action_text}", "操作成功", "success"])
                return OperationResult.ok(action, {
                    "campaign_id": campaign_id,
                    "action": action_text,
                    "verified": verified,
                })
            finally:
                page.remove_listener("dialog", _handle_dialog)
        except Exception as e:
            return OperationResult.fail(action, str(e), ErrorCode.PLATFORM_ERROR)

    async def get_ad_stats(self, campaign_id: str) -> OperationResult:
        try:
            page = await self._get_page()
            url = f"{AD_DETAIL_URL}?id={_url_quote(str(campaign_id))}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("ad_stats", "无法打开广告详情", ErrorCode.PLATFORM_ERROR)
            await page.wait_for_load_state("networkidle", timeout=10000)
            snapshot = await self._page_snapshot(page)
            return OperationResult.ok("ad_stats", {
                "campaign_id": campaign_id,
                "page_snapshot": snapshot[:2000],
                "url": page.url,
            })
        except Exception as e:
            return OperationResult.fail("ad_stats", str(e), ErrorCode.PLATFORM_ERROR)

    async def adjust_ad_budget(self, campaign_id: str, new_budget: float) -> OperationResult:
        try:
            page = await self._get_page()
            url = f"{AD_DETAIL_URL}?id={_url_quote(str(campaign_id))}"
            if not await self._safe_goto(page, url):
                return OperationResult.fail("adjust_budget", "无法打开广告详情", ErrorCode.PLATFORM_ERROR)
            await page.wait_for_load_state("networkidle", timeout=10000)
            if await self._fill_input(page, "预算", str(new_budget)):
                snapshot = await self._page_snapshot(page)
                return OperationResult.ok("adjust_budget", {
                    "status": "preview",
                    "campaign_id": campaign_id,
                    "new_budget": new_budget,
                    "page_snapshot": snapshot[:1500],
                    "instruction": "请确认预算修改后，调用 browser_action 点击保存",
                })
            return OperationResult.fail("adjust_budget", "未找到预算输入框", ErrorCode.PLATFORM_ERROR)
        except Exception as e:
            return OperationResult.fail("adjust_budget", str(e), ErrorCode.PLATFORM_ERROR)
