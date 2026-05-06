"""将 HTML 原型/UI设计文件渲染为 PNG 截图."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False


async def render_html_to_image(
    html_path: Path, output_path: Optional[Path] = None
) -> Optional[Path]:
    """用 Playwright 将 HTML 文件渲染为 PNG 截图."""
    if not HAS_PLAYWRIGHT:
        logger.info("Playwright 未安装，跳过截图: %s", html_path.name)
        return None

    if output_path is None:
        output_path = html_path.with_suffix(".png")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(viewport={"width": 375, "height": 812})
            await page.goto(f"file://{html_path.resolve()}")
            await page.wait_for_load_state("networkidle")
            await page.screenshot(path=str(output_path), full_page=True)
            await browser.close()
        return output_path
    except Exception as e:
        logger.warning("渲染 %s 失败: %s", html_path.name, e)
        return None


async def render_all_prototypes(
    project_dir: Path, subdir: str = "prototypes"
) -> list[Path]:
    """渲染目录下所有 HTML 文件为截图."""
    if not HAS_PLAYWRIGHT:
        return []

    html_dir = project_dir / subdir
    if not html_dir.exists():
        return []

    screenshots: list[Path] = []
    for html_file in sorted(html_dir.glob("*.html")):
        img = await render_html_to_image(html_file)
        if img:
            screenshots.append(img)
    return screenshots
