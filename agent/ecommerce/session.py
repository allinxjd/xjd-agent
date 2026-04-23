"""浏览器会话管理 — 全局单例.

连接优先级:
1. CDP 连接已有 Chromium (port 9333, 9222-9224)
2. 启动 persistent context Chromium (带 CDP port，下次可复用)

首次启动时用户需手动登录一次，之后 Chromium 持久运行，
下次通过 CDP 直接复用已登录的会话。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_instance: Optional["BrowserSessionManager"] = None


def get_session_manager() -> "BrowserSessionManager":
    """获取全局单例 BrowserSessionManager."""
    global _instance
    if _instance is None:
        _instance = BrowserSessionManager()
    return _instance


class BrowserSession:
    """单个浏览器会话."""

    def __init__(
        self,
        platform: str,
        account: str,
        context: Any = None,
        page: Any = None,
    ) -> None:
        self.platform = platform
        self.account = account
        self.context = context
        self.page = page

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.account}"

    async def close(self) -> None:
        if self.page and not self.page.is_closed():
            await self.page.close()
        self.page = None


class BrowserSessionManager:
    """管理多平台多账号的浏览器会话 (全局单例).

    首次启动 Chromium 后保持运行 (CDP port 9333)，
    用户手动登录一次，之后每次通过 CDP 直接复用。
    """

    _OUR_CDP_PORT = 9333

    _PLATFORM_DOMAINS: dict[str, list[str]] = {
        "pdd": ["mms.pinduoduo.com", "pinduoduo.com"],
        "taobao": ["myseller.taobao.com", "seller.taobao.com"],
        "jd": ["shop.jd.com", "sz.jd.com"],
        "douyin": ["buyin.jinritemai.com", "fxg.jinritemai.com"],
    }

    def __init__(self) -> None:
        from agent.core.config import get_home
        self._data_dir = get_home() / "ecommerce"
        self._profile_dir = get_home() / "browser-profile"
        self._profile_dir.mkdir(parents=True, exist_ok=True)

        self._sessions: dict[str, BrowserSession] = {}
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._cdp_connected = False
        self._our_chromium_launched = False

    # PLACEHOLDER_METHODS

    async def _ensure_browser(self) -> None:
        """懒加载浏览器: CDP 探测 → 启动 persistent context."""
        if self._browser or self._context:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "playwright 未安装。请运行:\n"
                "  pip install 'xjd-agent[browser]'\n"
                "  playwright install chromium"
            )
        self._playwright = await async_playwright().start()

        # CDP 探测: 先连我们的 Chromium (9333)，再试常见端口
        for port in (self._OUR_CDP_PORT, 9222, 9223, 9224):
            try:
                url = f"http://localhost:{port}"
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    url, timeout=3000,
                )
                self._cdp_connected = True
                if self._browser.contexts:
                    self._context = self._browser.contexts[0]
                else:
                    self._context = await self._browser.new_context()
                logger.info("CDP 连接成功: %s", url)
                return
            except Exception:
                continue

        # CDP 连不上 → 启动 persistent context
        await self._launch_persistent_context()
        self._our_chromium_launched = True

    async def _launch_persistent_context(self) -> None:
        """启动 Chromium，带 CDP 端口供下次复用，只保留一个标签页."""
        from agent.tools.browser import STEALTH_SCRIPTS
        logger.info(
            "启动 Chromium (CDP port=%d, profile=%s)",
            self._OUR_CDP_PORT, self._profile_dir,
        )
        self._context = await self._playwright.chromium.launch_persistent_context(
            str(self._profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 720},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                f"--remote-debugging-port={self._OUR_CDP_PORT}",
            ],
        )
        for script in STEALTH_SCRIPTS:
            await self._context.add_init_script(script)
        self._cdp_connected = False

        # 关闭 profile 恢复的多余标签页，只保留一个
        pages = self._context.pages
        if len(pages) > 1:
            for p in pages[1:]:
                try:
                    await p.close()
                except Exception:
                    pass

    # PLACEHOLDER_SESSION

    async def get_session(
        self, platform: str, account: str = "default",
    ) -> BrowserSession:
        """获取或创建浏览器会话 — 始终复用同一个标签页."""
        key = f"{platform}:{account}"
        session = self._sessions.get(key)
        if session and session.page and not session.page.is_closed():
            return session

        await self._ensure_browser()

        # CDP 模式: 查找已打开的目标平台标签页
        if self._cdp_connected:
            domains = self._PLATFORM_DOMAINS.get(platform, [])
            for page in self._context.pages:
                try:
                    page_url = page.url or ""
                    if any(d in page_url for d in domains):
                        session = BrowserSession(platform, account, self._context, page)
                        self._sessions[key] = session
                        logger.info("CDP: 复用已有标签页 %s", page_url[:80])
                        return session
                except Exception:
                    continue

        # 复用已有的第一个标签页，绝不新开标签
        page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        session = BrowserSession(platform, account, self._context, page)
        self._sessions[key] = session
        return session

    async def save_cookies(self, platform: str, account: str = "default") -> None:
        """持久化 cookies (persistent context 自动保存，此方法仅用于 CDP 模式)."""
        if self._cdp_connected:
            key = f"{platform}:{account}"
            session = self._sessions.get(key)
            if session and session.context:
                cookies = await session.context.cookies()
                cookie_dir = self._data_dir / platform / account
                cookie_dir.mkdir(parents=True, exist_ok=True)
                cookie_file = cookie_dir / "cookies.json"
                cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))

    async def close_all(self) -> None:
        """关闭会话，但保持我们启动的 Chromium 运行以便下次 CDP 复用."""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()

        if self._our_chromium_launched:
            logger.info("保持 Chromium 运行 (CDP port=%d)，下次可直接复用", self._OUR_CDP_PORT)
            self._context = None
            self._browser = None
        else:
            if self._context and not self._cdp_connected:
                await self._context.close()
            self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def force_close_all(self) -> None:
        """强制关闭所有浏览器（包括我们启动的 Chromium）."""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        if self._context and not self._cdp_connected:
            try:
                await self._context.close()
            except Exception:
                pass
        self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        self._our_chromium_launched = False
