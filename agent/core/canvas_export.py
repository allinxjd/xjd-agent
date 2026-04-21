"""Canvas 导出引擎 — HTML / PDF / PNG.

PDF/PNG 通过 Playwright headless Chromium 渲染。
Playwright 为可选依赖 (pyproject.toml: browser extra)，
未安装时 HTML 导出仍可用，PDF/PNG 返回友好错误。
"""

from __future__ import annotations

import logging
from typing import Optional

from .canvas import CanvasManager

logger = logging.getLogger(__name__)


class CanvasExporter:
    """Canvas 多格式导出."""

    def __init__(self, canvas_mgr: CanvasManager) -> None:
        self._mgr = canvas_mgr

    async def export_html(self, artifact_id: str) -> Optional[bytes]:
        html = self._mgr.render_html(artifact_id)
        if html is None:
            return None
        return html.encode("utf-8")

    async def export_pdf(self, artifact_id: str) -> Optional[bytes]:
        html = self._mgr.render_html(artifact_id)
        if html is None:
            return None
        return await self._render_with_playwright(html, "pdf")

    async def export_png(
        self, artifact_id: str, width: int = 1280, height: int = 720
    ) -> Optional[bytes]:
        html = self._mgr.render_html(artifact_id)
        if html is None:
            return None
        return await self._render_with_playwright(
            html, "png", width=width, height=height
        )

    async def _render_with_playwright(
        self, html: str, fmt: str = "pdf", **opts
    ) -> bytes:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright 未安装，无法导出 PDF/PNG。"
                "请运行: pip install 'xjd-agent[browser]' && playwright install chromium"
            )

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(
                    viewport={
                        "width": opts.get("width", 1280),
                        "height": opts.get("height", 720),
                    }
                )
                await page.set_content(html, wait_until="domcontentloaded", timeout=15000)
                # Brief wait for JS rendering (charts, mermaid, etc.)
                await page.wait_for_timeout(1500)
                # Force all elements visible (some pages use JS animations that
                # set opacity:0 on load, which won't trigger with set_content)
                await page.evaluate("""() => {
                    document.querySelectorAll('*').forEach(el => {
                        const s = getComputedStyle(el);
                        if (s.opacity === '0') el.style.opacity = '1';
                        if (s.display === 'none' && !el.id?.startsWith('_cdn_err'))
                            el.style.display = '';
                    });
                }""")
                await page.wait_for_timeout(500)
                if fmt == "pdf":
                    return await page.pdf(
                        format="A4", print_background=True
                    )
                else:
                    return await page.screenshot(
                        full_page=True, type="png"
                    )
            except Exception as e:
                raise RuntimeError(f"Playwright 渲染失败: {e}") from e
            finally:
                await browser.close()
