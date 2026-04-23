"""浏览器会话管理 — 全局单例 + persistent profile.

参考 browser-use / Skyvern 的最佳实践:
1. 全局单例: 整个进程只有一个 BrowserSessionManager 和一个浏览器实例
2. Persistent context: 用 user-data-dir 保持登录状态，重启后无需重新登录
3. 复用 page: 同一平台同一账号始终复用同一个标签页，不开新窗口
4. CDP 优先: 尝试连接用户已打开的 Chrome，连不上才用内置 Chromium
5. 绝不关闭用户浏览器
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 全局单例
_instance: Optional[BrowserSessionManager] = None


def get_session_manager() -> BrowserSessionManager:
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
    """管理多平台多账号的浏览器会话 (全局单例)."""

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

    _OUR_CDP_PORT = 9333

    async def _ensure_browser(self) -> None:
        """懒加载浏览器 (只创建一次).

        优先级:
        1. CDP 连接我们之前启动的 Chromium (port 9333)
        2. CDP 连接用户的 Chrome (port 9222-9224)
        3. 启动新的 persistent context Chromium (带 --remote-debugging-port=9333)
        """
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

        # 1. CDP: 先连我们自己的 Chromium (9333)，再连用户 Chrome (9222-9224)
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
                logger.info("CDP 连接成功: %s (复用已有浏览器)", url)
                return
            except Exception:
                continue

        # 2. 没有可连接的浏览器 → 启动新的 (带 CDP 端口，下次可连回来)
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

    _PLATFORM_DOMAINS: dict[str, list[str]] = {
        "pdd": ["mms.pinduoduo.com"],
        "taobao": ["myseller.taobao.com", "seller.taobao.com"],
        "jd": ["shop.jd.com", "sz.jd.com"],
        "douyin": ["buyin.jinritemai.com", "fxg.jinritemai.com"],
    }

    async def get_session(
        self, platform: str, account: str = "default",
    ) -> BrowserSession:
        """获取或创建浏览器会话 (复用已有 page，不开新窗口)."""
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

        # 复用已有空白页 or 新开一个 (在同一个 context 里，不开新窗口)
        page = None
        for p in self._context.pages:
            if p.url in ("about:blank", "chrome://newtab/", ""):
                page = p
                break
        if not page:
            page = await self._context.new_page()
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
        """关闭所有会话和浏览器."""
        for session in self._sessions.values():
            await session.close()
        self._sessions.clear()
        if self._context and not self._cdp_connected:
            await self._context.close()
        self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
