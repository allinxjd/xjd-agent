"""浏览器会话管理 — 多平台多账号的 Playwright 会话池.

独立于 agent/tools/browser.py 的全局单例，
电商模块使用独立的 BrowserContext pool，支持:
- 每个 (platform, account) 一个隔离的 BrowserContext
- Cookie 持久化 (避免频繁登录)
- 会话健康检查 + 自动重连
- Stealth 模式默认开启
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


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
        if self.context:
            await self.context.close()
        self.page = None
        self.context = None


class BrowserSessionManager:
    """管理多平台多账号的浏览器会话."""

    def __init__(self, data_dir: Optional[str] = None) -> None:
        if data_dir:
            self._data_dir = Path(data_dir)
        else:
            from agent.core.config import get_home
            self._data_dir = get_home() / "ecommerce"

        self._sessions: dict[str, BrowserSession] = {}
        self._playwright: Any = None
        self._browser: Any = None

    def _cookie_path(self, platform: str, account: str) -> Path:
        p = self._data_dir / platform / account
        p.mkdir(parents=True, exist_ok=True)
        return p / "cookies.json"

    async def _ensure_browser(self) -> None:
        """懒加载 Playwright + Chromium."""
        if self._browser:
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
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def get_session(
        self, platform: str, account: str = "default",
    ) -> BrowserSession:
        """获取或创建浏览器会话."""
        key = f"{platform}:{account}"
        session = self._sessions.get(key)
        if session and session.page and not session.page.is_closed():
            return session

        await self._ensure_browser()

        from agent.tools.browser import STEALTH_SCRIPTS

        context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        for script in STEALTH_SCRIPTS:
            await context.add_init_script(script)

        # 恢复 cookies
        cookie_file = self._cookie_path(platform, account)
        if cookie_file.exists():
            try:
                cookies = json.loads(cookie_file.read_text())
                await context.add_cookies(cookies)
                logger.info("Restored cookies for %s", key)
            except Exception as e:
                logger.warning("Failed to restore cookies for %s: %s", key, e)

        page = await context.new_page()
        session = BrowserSession(platform, account, context, page)
        self._sessions[key] = session
        return session

    async def save_cookies(self, platform: str, account: str = "default") -> None:
        """持久化当前会话的 cookies."""
        key = f"{platform}:{account}"
        session = self._sessions.get(key)
        if not session or not session.context:
            return
        cookies = await session.context.cookies()
        cookie_file = self._cookie_path(platform, account)
        cookie_file.write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
        logger.debug("Saved %d cookies for %s", len(cookies), key)

    async def close_session(self, platform: str, account: str = "default") -> None:
        """关闭指定会话."""
        key = f"{platform}:{account}"
        session = self._sessions.pop(key, None)
        if session:
            await session.close()

    async def close_all(self) -> None:
        """关闭所有会话和浏览器."""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
