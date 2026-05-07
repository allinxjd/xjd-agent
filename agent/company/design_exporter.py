"""DesignExporter — UI 设计稿导出引擎（PNG / PDF / MP4）.

将 ui-designs/ 目录下的 HTML 文件导出为：
- PNG 截图（飞书预览用）
- PDF 文档（设计交付物）
- MP4 视频（演示用，可选，需 ffmpeg）
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

HAS_FFMPEG = shutil.which("ffmpeg") is not None


class DesignExporter:
    """UI 设计稿多格式导出."""

    def __init__(self, viewport: tuple[int, int] = (375, 812)) -> None:
        self._viewport_w, self._viewport_h = viewport

    async def export_png(
        self, html_path: Path, output_path: Optional[Path] = None
    ) -> Optional[Path]:
        """将单个 HTML 渲染为 PNG 截图."""
        if not HAS_PLAYWRIGHT:
            logger.warning("Playwright 未安装，跳过 PNG 导出: %s", html_path.name)
            return None

        if output_path is None:
            output_path = html_path.with_suffix(".png")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(
                    viewport={"width": self._viewport_w, "height": self._viewport_h}
                )
                await page.goto(f"file://{html_path.resolve()}", wait_until="networkidle")
                await page.wait_for_timeout(1000)
                await page.screenshot(path=str(output_path), full_page=True)
                await browser.close()
            return output_path
        except Exception as e:
            logger.warning("PNG 导出失败 [%s]: %s", html_path.name, e)
            return None

    async def export_pdf(
        self, html_path: Path, output_path: Optional[Path] = None
    ) -> Optional[Path]:
        """将单个 HTML 渲染为 A4 PDF."""
        if not HAS_PLAYWRIGHT:
            logger.warning("Playwright 未安装，跳过 PDF 导出: %s", html_path.name)
            return None

        if output_path is None:
            output_path = html_path.with_suffix(".pdf")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(f"file://{html_path.resolve()}", wait_until="networkidle")
                await page.wait_for_timeout(1000)
                await page.pdf(path=str(output_path), format="A4", print_background=True)
                await browser.close()
            return output_path
        except Exception as e:
            logger.warning("PDF 导出失败 [%s]: %s", html_path.name, e)
            return None

    async def export_pdf_multi(
        self, html_paths: list[Path], output: Path
    ) -> Optional[Path]:
        """将多个 HTML 合并为单个 PDF（每页一个 HTML）."""
        if not HAS_PLAYWRIGHT:
            logger.warning("Playwright 未安装，跳过多页 PDF 导出")
            return None
        if not html_paths:
            return None

        try:
            from pypdf import PdfMerger
        except ImportError:
            logger.info("pypdf 未安装，降级为单文件 PDF（仅首页）")
            return await self.export_pdf(html_paths[0], output)

        tmp_pdfs: list[Path] = []
        try:
            for html_path in html_paths:
                tmp_pdf = html_path.with_suffix(".tmp.pdf")
                result = await self.export_pdf(html_path, tmp_pdf)
                if result:
                    tmp_pdfs.append(tmp_pdf)

            if not tmp_pdfs:
                return None

            merger = PdfMerger()
            for pdf in tmp_pdfs:
                merger.append(str(pdf))
            merger.write(str(output))
            merger.close()
            return output
        except Exception as e:
            logger.warning("多页 PDF 合并失败: %s", e)
            if tmp_pdfs:
                return await self.export_pdf(html_paths[0], output)
            return None
        finally:
            for tmp in tmp_pdfs:
                tmp.unlink(missing_ok=True)

    async def export_mp4(
        self,
        html_paths: list[Path],
        output: Path,
        seconds_per_frame: int = 3,
    ) -> Optional[Path]:
        """将多个 HTML 截图合成 MP4 视频（需 ffmpeg）."""
        if not HAS_FFMPEG:
            logger.info("ffmpeg 未安装，跳过 MP4 导出")
            return None
        if not HAS_PLAYWRIGHT:
            logger.warning("Playwright 未安装，跳过 MP4 导出")
            return None
        if not html_paths:
            return None

        tmp_dir = Path(tempfile.mkdtemp(prefix="design_mp4_"))
        try:
            frames: list[Path] = []
            for i, html_path in enumerate(html_paths):
                frame_path = tmp_dir / f"frame_{i:03d}.png"
                result = await self.export_png(html_path, frame_path)
                if result:
                    frames.append(result)

            if not frames:
                return None

            concat_file = tmp_dir / "concat.txt"
            lines = []
            for frame in frames:
                lines.append(f"file '{frame}'")
                lines.append(f"duration {seconds_per_frame}")
            lines.append(f"file '{frames[-1]}'")
            concat_file.write_text("\n".join(lines))

            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p",
                "-c:v", "libx264", "-preset", "fast",
                "-movflags", "+faststart",
                str(output),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning("ffmpeg 失败: %s", stderr.decode()[-500:])
                return None
            return output
        except Exception as e:
            logger.warning("MP4 导出失败: %s", e)
            return None
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    async def export_all(
        self, ui_dir: Path
    ) -> dict[str, list[Path] | Optional[Path]]:
        """导出 ui-designs/ 目录下所有 HTML 的 PNG + PDF + MP4.

        Returns dict with keys: "screenshots", "pdf", "mp4"
        """
        html_files = sorted(ui_dir.glob("*.html"))
        if not html_files:
            logger.info("ui-designs/ 目录无 HTML 文件，跳过导出")
            return {"screenshots": [], "pdf": None, "mp4": None}

        screenshots: list[Path] = []
        for html in html_files:
            png = await self.export_png(html)
            if png:
                screenshots.append(png)

        pdf = await self.export_pdf_multi(html_files, ui_dir / "ui-design.pdf")
        mp4 = await self.export_mp4(html_files, ui_dir / "ui-design.mp4")

        return {"screenshots": screenshots, "pdf": pdf, "mp4": mp4}
