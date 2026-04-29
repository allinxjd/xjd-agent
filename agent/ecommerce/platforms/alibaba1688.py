"""1688 选品采集适配器 — Playwright 浏览器自动化.

从 1688.com 采集商品数据，支持:
- 关键词搜索 + 多维筛选
- 以图搜货（用户上传 / PDD 竞品链接）
- 商品详情 + SKU 提取
- 供应商信誉查询
- 商品图片批量下载
- 1688→PDD 商品草稿生成

写操作安全策略: 1688 作为采购源，只有读操作，无写操作风险。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote as _url_quote, urljoin

from agent.ecommerce.base import EcommercePlatform
from agent.ecommerce.platforms import register_platform
from agent.ecommerce.protocol import (
    ErrorCode, OperationResult, Product,
)

logger = logging.getLogger(__name__)

LOGIN_URL = "https://login.1688.com/member/signin.htm"
HOME_URL = "https://www.1688.com"
SEARCH_URL = "https://s.1688.com/selloffer/offer_search.htm"
IMAGE_SEARCH_URL = "https://s.1688.com/youyuan/index.htm"
DETAIL_URL_TPL = "https://detail.1688.com/offer/{}.html"
SUPPLIER_URL_TPL = "https://shop{}.1688.com"

ASSETS_DIR_NAME = "1688-assets"


def _assets_dir() -> Path:
    from agent.core.config import get_home
    d = get_home() / ASSETS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


@register_platform
class Alibaba1688Platform(EcommercePlatform):
    """1688 选品采集适配器."""

    platform_name = "1688"
    BASE_URL = "https://www.1688.com"
    _nav_lock = asyncio.Lock()

    # ── 浏览器辅助 ──

    async def _get_page(self):
        if not self._session:
            from agent.ecommerce.session import get_session_manager
            self._session = get_session_manager()
        session = await self._session.get_session("1688")
        return session.page

    async def _safe_goto(self, page, url: str, timeout: int = 30000) -> bool:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            final_url = page.url or ""
            if "login.1688.com" in final_url and "login.1688.com" not in url:
                logger.warning("1688 session expired — redirected to login")
                return False
            return True
        except Exception as e:
            logger.warning("1688 navigate failed %s: %s", url, e)
            return False

    async def _page_snapshot(self, page, max_len: int = 3000) -> str:
        return await page.evaluate(
            f"() => document.body?.innerText?.substring(0, {max_len}) || ''"
        )

    async def _find_and_click(self, page, texts: list[str]) -> bool:
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

    # ── 认证 ──

    async def check_session(self) -> bool:
        try:
            page = await self._get_page()
            if not await self._safe_goto(page, HOME_URL, timeout=15000):
                return False
            text = await self._page_snapshot(page, 500)
            if "登录" in text and "我的阿里" not in text:
                return False
            return True
        except Exception:
            return False

    async def login(self, credentials: dict[str, Any]) -> OperationResult:
        try:
            page = await self._get_page()
            if await self.check_session():
                await self._session.save_cookies("1688")
                mode = "CDP" if self._session._cdp_connected else "内置 Chromium"
                return OperationResult.ok("login", {
                    "message": f"已登录 1688 [{mode}]",
                })

            if not credentials.get("force_new", False):
                restored = await self._try_restore_cookies(page)
                if restored:
                    return OperationResult.ok("login", {
                        "message": "已登录 1688 [Cookie 恢复]",
                    })

            if not await self._safe_goto(page, LOGIN_URL):
                return OperationResult.fail(
                    "login", "无法访问 1688 登录页", ErrorCode.NETWORK_ERROR,
                )
            mode = "CDP" if self._session._cdp_connected else "内置 Chromium"
            return OperationResult.ok("login", {
                "message": f"已打开 1688 登录页 [{mode}]，请在浏览器中扫码或输入账号密码登录",
                "url": LOGIN_URL,
                "status": "waiting_for_user",
            })
        except Exception as e:
            return OperationResult.fail("login", f"登录失败: {e}", ErrorCode.PLATFORM_ERROR)

    async def _try_restore_cookies(self, page) -> bool:
        try:
            accounts = self._session.list_accounts("1688")
            if not accounts:
                return False
            for acc in sorted(
                accounts,
                key=lambda a: (
                    self._session._data_dir / "1688" / a / "cookies.json"
                ).stat().st_mtime,
                reverse=True,
            ):
                saved = self._session.load_cookies("1688", acc)
                if not saved:
                    continue
                ali_cookies = [c for c in saved if "1688" in c.get("domain", "")]
                if not ali_cookies:
                    continue
                ctx = page.context
                await ctx.add_cookies(ali_cookies)
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=15000)
                text = await self._page_snapshot(page, 500)
                if "我的阿里" in text or "已登录" in text:
                    logger.info("1688 cookie restore succeeded for %s", acc)
                    return True
            return False
        except Exception as e:
            logger.debug("1688 cookie restore failed: %s", e)
            return False

    # ── AI 验证码 ──

    async def _solve_captcha(self, page, max_attempts: int = 3) -> bool:
        """检测并用 AI 视觉模型解决验证码."""
        for attempt in range(max_attempts):
            captcha_frame = page.locator("#baxia-dialog-content, .nc-container, #nocaptcha")
            if await captcha_frame.count() == 0:
                return True

            logger.info("1688 captcha detected, attempt %d", attempt + 1)
            screenshot = await page.screenshot(type="png")

            try:
                from agent.tools.extended import _vision_analyze_impl
                result = await _vision_analyze_impl({
                    "image": screenshot,
                    "prompt": (
                        "这是一个网页验证码截图。"
                        "如果是滑块验证码，返回JSON: {\"type\":\"slider\",\"distance\":像素数}"
                        "如果是文字点选验证码，返回JSON: {\"type\":\"text\",\"chars\":[\"字1\",\"字2\",...]}"
                        "如果没有验证码，返回JSON: {\"type\":\"none\"}"
                    ),
                })
                import json as _json
                info = _json.loads(result.get("analysis", "{}"))
            except Exception as e:
                logger.warning("AI captcha analysis failed: %s", e)
                await page.wait_for_timeout(2000)
                continue

            captcha_type = info.get("type", "none")
            if captcha_type == "none":
                return True

            if captcha_type == "slider":
                await self._slide_captcha(page, info.get("distance", 260))
            elif captcha_type == "text":
                logger.info("Text captcha detected, requesting user intervention")
                return False

            await page.wait_for_timeout(2000)
            if await captcha_frame.count() == 0:
                return True

        logger.warning("Captcha not solved after %d attempts", max_attempts)
        return False

    async def _slide_captcha(self, page, distance: int) -> None:
        """执行滑块验证码."""
        import random
        slider = page.locator(".btn_slide, .nc_iconfont, .slider, #nc_1_n1z")
        if await slider.count() == 0:
            return
        box = await slider.first.bounding_box()
        if not box:
            return
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        await page.mouse.move(x, y)
        await page.mouse.down()
        steps = random.randint(20, 35)
        for i in range(steps):
            progress = (i + 1) / steps
            ease = progress * (2 - progress)
            await page.mouse.move(
                x + distance * ease + random.uniform(-2, 2),
                y + random.uniform(-1, 1),
                steps=1,
            )
            await page.wait_for_timeout(random.randint(5, 20))
        await page.mouse.up()

    # ── 关键词搜索 ──

    async def search_by_keyword(
        self, keyword: str, filters: Optional[dict[str, Any]] = None,
    ) -> OperationResult:
        """关键词搜索 1688 商品，支持筛选."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail(
                    "search_by_keyword", "未登录 1688", ErrorCode.AUTH_REQUIRED,
                )

            filters = filters or {}
            params = f"keywords={_url_quote(keyword)}"
            if filters.get("price_min"):
                params += f"&e_price_b={filters['price_min']}"
            if filters.get("price_max"):
                params += f"&e_price_e={filters['price_max']}"
            if filters.get("region"):
                params += f"&province={_url_quote(filters['region'])}"
            sort_map = {
                "sales": "va_sales",
                "price_asc": "va_price",
                "price_desc": "va_rp",
                "credit": "va_credit",
            }
            if filters.get("sort") and filters["sort"] in sort_map:
                params += f"&sortType={sort_map[filters['sort']]}"

            url = f"{SEARCH_URL}?{params}"
            captured: list[dict] = []

            async def _on_response(response):
                try:
                    if ("offer_search" in response.url or "normalOffer" in response.url
                            or "offerSearch" in response.url) and response.status == 200:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct or "javascript" in ct:
                            body = await response.text()
                            body = self._extract_json_from_jsonp(body)
                            if isinstance(body, dict):
                                captured.append(body)
                except Exception:
                    pass

            page.on("response", _on_response)
            try:
                async with self._nav_lock:
                    await self._safe_goto(page, url)
                    await self._solve_captcha(page)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
            finally:
                page.remove_listener("response", _on_response)

            products = self._parse_search_results(captured)
            if not products:
                products = await self._extract_search_from_dom(page)

            return OperationResult.ok("search_by_keyword", {
                "keyword": keyword,
                "total": len(products),
                "products": products[:40],
            })
        except Exception as e:
            return OperationResult.fail(
                "search_by_keyword", f"搜索失败: {e}", ErrorCode.PLATFORM_ERROR,
            )

    async def list_products(
        self, filters: Optional[dict[str, Any]] = None,
    ) -> OperationResult:
        keyword = (filters or {}).get("keyword", "")
        if not keyword:
            return OperationResult.fail(
                "list_products", "请提供搜索关键词 (filters.keyword)", ErrorCode.INVALID_PARAMS,
            )
        return await self.search_by_keyword(keyword, filters)

    # ── 以图搜货 ──

    async def search_by_image(self, image_source: str) -> OperationResult:
        """以图搜货 — 支持本地图片路径或 URL."""
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail(
                    "search_by_image", "未登录 1688", ErrorCode.AUTH_REQUIRED,
                )

            image_path = image_source
            if image_source.startswith("http"):
                image_path = await self._download_temp_image(image_source)

            async with self._nav_lock:
                await self._safe_goto(page, IMAGE_SEARCH_URL)
                await page.wait_for_timeout(2000)

                file_input = page.locator('input[type="file"]')
                if await file_input.count() == 0:
                    return OperationResult.fail(
                        "search_by_image", "未找到图片上传入口", ErrorCode.PLATFORM_ERROR,
                    )
                await file_input.first.set_input_files(image_path)
                await page.wait_for_timeout(5000)
                await self._solve_captcha(page)

                try:
                    await page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass
                await page.wait_for_timeout(3000)

            products = await self._extract_search_from_dom(page)
            return OperationResult.ok("search_by_image", {
                "source": image_source,
                "total": len(products),
                "products": products[:40],
            })
        except Exception as e:
            return OperationResult.fail(
                "search_by_image", f"以图搜货失败: {e}", ErrorCode.PLATFORM_ERROR,
            )

    # ── 商品详情 ──

    async def get_product(self, product_id: str) -> OperationResult:
        try:
            page = await self._get_page()
            if not await self.check_session():
                return OperationResult.fail(
                    "get_product", "未登录 1688", ErrorCode.AUTH_REQUIRED,
                )

            url = DETAIL_URL_TPL.format(product_id)
            captured: list[dict] = []

            async def _on_response(response):
                try:
                    if ("offerDetailData" in response.url or "offer/ajax" in response.url
                            or "offerdetail" in response.url.lower()) and response.status == 200:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct or "javascript" in ct:
                            body = await response.text()
                            body = self._extract_json_from_jsonp(body)
                            if isinstance(body, dict):
                                captured.append(body)
                except Exception:
                    pass

            page.on("response", _on_response)
            try:
                async with self._nav_lock:
                    await self._safe_goto(page, url)
                    await self._solve_captcha(page)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
            finally:
                page.remove_listener("response", _on_response)

            product = self._parse_detail_api(captured, product_id)
            if not product:
                product = await self._extract_detail_from_dom(page, product_id)

            if not product:
                return OperationResult.fail(
                    "get_product", f"未找到商品 {product_id}", ErrorCode.ITEM_NOT_FOUND,
                )
            return OperationResult.ok("get_product", product)
        except Exception as e:
            return OperationResult.fail(
                "get_product", f"获取商品详情失败: {e}", ErrorCode.PLATFORM_ERROR,
            )

    # ── 供应商信息 ──

    async def get_supplier_info(self, supplier_id: str) -> OperationResult:
        try:
            page = await self._get_page()
            url = f"https://winport.1688.com/page/offerlist.htm?memberId={supplier_id}"
            async with self._nav_lock:
                await self._safe_goto(page, url)
                await page.wait_for_timeout(3000)

            text = await self._page_snapshot(page, 5000)
            info: dict[str, Any] = {"supplier_id": supplier_id, "raw_text": text[:1000]}

            for pattern, key in [
                (r"经营模式[：:]\s*(\S+)", "business_mode"),
                (r"所在地区[：:]\s*(.+?)(?:\n|$)", "location"),
                (r"成立年份[：:]\s*(\d+)", "founded_year"),
                (r"年营业额[：:]\s*(.+?)(?:\n|$)", "annual_revenue"),
                (r"主营[产商]品[：:]\s*(.+?)(?:\n|$)", "main_products"),
            ]:
                m = re.search(pattern, text)
                if m:
                    info[key] = m.group(1).strip()

            return OperationResult.ok("get_supplier_info", info)
        except Exception as e:
            return OperationResult.fail(
                "get_supplier_info", f"获取供应商信息失败: {e}", ErrorCode.PLATFORM_ERROR,
            )

    # ── 图片资产下载 ──

    async def download_product_assets(self, product_id: str) -> OperationResult:
        """下载商品的所有图片（主图 + 详情图 + SKU 图）."""
        try:
            detail_result = await self.get_product(product_id)
            if not detail_result.success:
                return detail_result

            data = detail_result.data
            images = data.get("images", [])
            detail_images = data.get("detail_images", [])
            sku_images = []
            for sku in data.get("sku_list", []):
                img = sku.get("image", "")
                if img and img not in sku_images:
                    sku_images.append(img)

            all_urls = images + detail_images + sku_images
            if not all_urls:
                return OperationResult.fail(
                    "download_product_assets", "未找到可下载的图片", ErrorCode.ITEM_NOT_FOUND,
                )

            save_dir = _assets_dir() / product_id
            save_dir.mkdir(parents=True, exist_ok=True)

            downloaded = []
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                for i, url in enumerate(all_urls):
                    try:
                        resp = await client.get(url, headers={"Referer": "https://detail.1688.com/"})
                        if resp.status_code == 200:
                            ext = ".jpg"
                            ct = resp.headers.get("content-type", "")
                            if "png" in ct:
                                ext = ".png"
                            elif "webp" in ct:
                                ext = ".webp"
                            fname = f"{i:03d}{ext}"
                            fpath = save_dir / fname
                            fpath.write_bytes(resp.content)
                            downloaded.append(str(fpath))
                    except Exception as e:
                        logger.debug("Download image %d failed: %s", i, e)

            return OperationResult.ok("download_product_assets", {
                "product_id": product_id,
                "total": len(downloaded),
                "save_dir": str(save_dir),
                "files": downloaded,
            })
        except Exception as e:
            return OperationResult.fail(
                "download_product_assets", f"下载失败: {e}", ErrorCode.PLATFORM_ERROR,
            )

    # ── 数据解析辅助 ──

    @staticmethod
    def _extract_json_from_jsonp(text: str) -> Any:
        """从 JSONP 响应中提取 JSON 对象."""
        import json as _json
        text = text.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                return _json.loads(text)
            except Exception:
                pass
        m = re.search(r"\((\{.+\})\)\s*;?\s*$", text, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(1))
            except Exception:
                pass
        m = re.search(r"=\s*(\{.+\})\s*;?\s*$", text, re.DOTALL)
        if m:
            try:
                return _json.loads(m.group(1))
            except Exception:
                pass
        return text

    @staticmethod
    def _parse_search_results(captured: list[dict]) -> list[dict]:
        """从 API 响应中解析搜索结果."""
        products = []
        for body in captured:
            offer_list = None
            for path in [
                ["data", "offerList"],
                ["data", "data", "offerList"],
                ["content", "offerResult", "result"],
                ["data", "offerResultData", "offerList"],
            ]:
                node = body
                for key in path:
                    if isinstance(node, dict):
                        node = node.get(key)
                    else:
                        node = None
                        break
                if isinstance(node, list) and node:
                    offer_list = node
                    break

            if not offer_list:
                continue

            for item in offer_list:
                if not isinstance(item, dict):
                    continue
                offer_id = str(item.get("id", item.get("offerId", "")))
                if not offer_id:
                    continue
                price_raw = item.get("tradePrice", item.get("price", ""))
                price_str = str(price_raw).replace("¥", "").replace(",", "").strip()
                try:
                    if "-" in price_str:
                        price = float(price_str.split("-")[0])
                    else:
                        price = float(price_str) if price_str else 0.0
                except ValueError:
                    price = 0.0

                products.append({
                    "product_id": offer_id,
                    "title": item.get("subject", item.get("title", "")),
                    "price": price,
                    "price_range": str(price_raw),
                    "moq": item.get("quantityBegin", 1),
                    "sold": item.get("monthSold", item.get("gmvMonthCount", 0)),
                    "supplier": item.get("company", item.get("sellerName", "")),
                    "location": item.get("province", ""),
                    "image": item.get("imageUrl", item.get("image", {}).get("imgUrl", "")),
                    "url": DETAIL_URL_TPL.format(offer_id),
                })
        return products

    async def _extract_search_from_dom(self, page) -> list[dict]:
        """DOM 回退：从搜索结果页提取商品列表."""
        try:
            return await page.evaluate("""() => {
                const items = document.querySelectorAll(
                    '.offer-list-row .offer-list-item, .sm-offer-item, [data-offer-id]'
                );
                const results = [];
                items.forEach(el => {
                    const link = el.querySelector('a[href*="detail.1688.com"], a[href*="offer/"]');
                    const titleEl = el.querySelector('.title, .offer-title, h4, h3');
                    const priceEl = el.querySelector('.price, .sm-offer-priceNum');
                    const imgEl = el.querySelector('img[src*="cbu01"], img[src*="img.alicdn"]');
                    const id = el.dataset?.offerId
                        || (link?.href?.match(/offer\\/?(\\d+)/)?.[1])
                        || '';
                    if (!id) return;
                    results.push({
                        product_id: id,
                        title: titleEl?.textContent?.trim() || '',
                        price: parseFloat(priceEl?.textContent?.replace(/[^\\d.]/g, '') || '0'),
                        image: imgEl?.src || '',
                        url: 'https://detail.1688.com/offer/' + id + '.html',
                    });
                });
                return results.slice(0, 40);
            }""")
        except Exception as e:
            logger.debug("DOM search extraction failed: %s", e)
            return []

    @staticmethod
    def _parse_detail_api(captured: list[dict], product_id: str) -> Optional[dict]:
        """从 API 响应中解析商品详情."""
        for body in captured:
            data = body.get("data", body)
            if not isinstance(data, dict):
                continue
            offer = data.get("offerDetail", data.get("data", data))
            if not isinstance(offer, dict):
                continue

            title = offer.get("subject", offer.get("title", ""))
            if not title:
                continue

            images = []
            for img_item in offer.get("images", offer.get("imageList", [])):
                if isinstance(img_item, str):
                    images.append(img_item)
                elif isinstance(img_item, dict):
                    images.append(img_item.get("originalImageURI", img_item.get("imageUrl", "")))

            sku_list = []
            sku_data = offer.get("skuInfos", offer.get("skuList", []))
            for sku in (sku_data if isinstance(sku_data, list) else []):
                sku_list.append({
                    "sku_id": str(sku.get("skuId", "")),
                    "name": sku.get("specAttrs", sku.get("name", "")),
                    "price": float(sku.get("price", 0)),
                    "stock": int(sku.get("canBookCount", sku.get("stock", 0))),
                    "image": sku.get("imageUrl", ""),
                })

            price_info = offer.get("priceInfo", {})
            price_range = price_info.get("priceRange", [])
            price = 0.0
            if price_range and isinstance(price_range, list):
                price = float(price_range[0].get("price", 0))
            elif offer.get("tradePrice"):
                try:
                    price = float(str(offer["tradePrice"]).replace("¥", ""))
                except ValueError:
                    pass

            detail_images = []
            desc = offer.get("description", offer.get("detailContent", ""))
            if isinstance(desc, str):
                detail_images = re.findall(r'https?://[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', desc)

            return {
                "product_id": product_id,
                "title": title,
                "price": price,
                "price_range": str(price_info.get("priceRange", "")),
                "moq": offer.get("minOrderQuantity", offer.get("beginAmount", 1)),
                "images": images,
                "detail_images": detail_images[:20],
                "sku_list": sku_list,
                "category": offer.get("categoryName", ""),
                "supplier_id": str(offer.get("memberId", offer.get("sellerId", ""))),
                "supplier_name": offer.get("companyName", offer.get("sellerLoginId", "")),
                "location": offer.get("province", ""),
                "url": DETAIL_URL_TPL.format(product_id),
                "metadata": {
                    "unit": offer.get("unit", ""),
                    "saleCount": offer.get("saleCount", 0),
                    "repurchaseRate": offer.get("repurchaseRate", ""),
                },
            }
        return None

    async def _extract_detail_from_dom(self, page, product_id: str) -> Optional[dict]:
        """DOM 回退：从商品详情页提取数据."""
        try:
            return await page.evaluate("""(pid) => {
                const title = document.querySelector(
                    '.title-text, .d-title, h1.mod-detail-title'
                )?.textContent?.trim() || '';
                if (!title) return null;

                const priceEl = document.querySelector(
                    '.price-text, .d-content-price .value, .price-original-sku'
                );
                const price = parseFloat(priceEl?.textContent?.replace(/[^\\d.]/g, '') || '0');

                const images = [];
                document.querySelectorAll(
                    '.detail-gallery img, .tab-pane img[src*="cbu01"], .main-image img'
                ).forEach(img => {
                    const src = img.src || img.dataset?.src || '';
                    if (src && !images.includes(src)) images.push(src);
                });

                const skuList = [];
                document.querySelectorAll('.sku-item, .obj-sku .unit-detail-spec-operator').forEach(el => {
                    skuList.push({
                        name: el.textContent?.trim() || '',
                        image: el.querySelector('img')?.src || '',
                    });
                });

                return {
                    product_id: pid,
                    title,
                    price,
                    images: images.slice(0, 10),
                    sku_list: skuList,
                    url: location.href,
                };
            }""", product_id)
        except Exception as e:
            logger.debug("DOM detail extraction failed: %s", e)
            return None

    async def _download_temp_image(self, url: str) -> str:
        """下载临时图片用于以图搜货."""
        import httpx
        save_dir = _assets_dir() / "_temp"
        save_dir.mkdir(parents=True, exist_ok=True)
        fname = hashlib.md5(url.encode()).hexdigest()[:12] + ".jpg"
        fpath = save_dir / fname
        if fpath.exists():
            return str(fpath)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            fpath.write_bytes(resp.content)
        return str(fpath)
