"""浏览器会话管理 — 全局单例 + 多策略连接.

连接优先级:
1. CDP 连接已有 Chromium/Chrome (port 9333, 9222-9224)
2. macOS: AppleScript 检测用户 Chrome 是否已打开目标平台页面
   → 如果是，提取 Chrome cookies 注入 Playwright persistent context
3. 启动新的 persistent context Chromium (带 CDP port，下次可复用)

绝不关闭用户浏览器。
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import sqlite3
import subprocess
import tempfile
from hashlib import pbkdf2_hmac
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

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


# ── macOS Chrome cookie 提取 ──────────────────────────────────────

def _is_macos() -> bool:
    return platform.system() == "Darwin"


def _detect_chrome_tabs() -> list[str]:
    """macOS: 用 AppleScript 获取 Chrome 所有标签页 URL."""
    if not _is_macos():
        return []
    try:
        result = subprocess.run(
            ["osascript", "-e", """
tell application "System Events"
    if not (exists process "Google Chrome") then return ""
end tell
tell application "Google Chrome"
    set allURLs to {}
    repeat with w in windows
        repeat with t in tabs of w
            set end of allURLs to URL of t
        end repeat
    end repeat
    return allURLs
end tell
"""],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return [u.strip() for u in result.stdout.strip().split(", ") if u.strip()]
    except Exception as e:
        logger.debug("AppleScript Chrome 检测失败: %s", e)
    return []


def _chrome_has_platform_page(platform_name: str, domains: list[str]) -> bool:
    """检测用户 Chrome 是否已打开目标平台页面."""
    urls = _detect_chrome_tabs()
    for url in urls:
        if any(d in url for d in domains):
            logger.info("检测到 Chrome 已打开 %s 页面: %s", platform_name, url[:80])
            return True
    return False


def _extract_chrome_cookies(domains: list[str]) -> list[dict]:
    """从 Chrome cookie 数据库提取指定域名的 cookies (macOS).

    需要 cryptography 库。首次调用时 macOS 可能弹出 Keychain 授权弹窗。
    """
    if not _is_macos():
        return []

    chrome_cookie_db = Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies"
    if not chrome_cookie_db.exists():
        return []

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.backends import default_backend
    except ImportError:
        logger.debug("cryptography 未安装，跳过 Chrome cookie 提取")
        return []

    # 从 Keychain 获取 Chrome 加密密钥
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w",
             "-s", "Chrome Safe Storage", "-a", "Chrome"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            logger.debug("无法获取 Chrome Keychain 密钥")
            return []
        password = result.stdout.strip()
    except Exception:
        return []

    key = pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1003, dklen=16)

    def _decrypt(encrypted_value: bytes) -> str:
        if not encrypted_value or len(encrypted_value) < 4:
            return ""
        if encrypted_value[:3] == b"v10":
            try:
                iv = b" " * 16
                cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                decryptor = cipher.decryptor()
                decrypted = decryptor.update(encrypted_value[3:]) + decryptor.finalize()
                padding_len = decrypted[-1]
                if 0 < padding_len <= 16:
                    text = decrypted[:-padding_len].decode("utf-8", errors="replace")
                    # 过滤掉解密失败产生的乱码
                    if "\ufffd" in text:
                        return ""
                    return text
            except Exception:
                pass
        return ""

    # 复制 DB 避免锁冲突
    tmp_db = Path(tempfile.mktemp(suffix=".db"))
    try:
        shutil.copy2(str(chrome_cookie_db), str(tmp_db))
        conn = sqlite3.connect(str(tmp_db))

        host_clauses = " OR ".join(
            f"host_key LIKE '%{d}%'" for d in domains
        )
        cur = conn.execute(
            f"SELECT host_key, name, encrypted_value, path, is_secure, "
            f"is_httponly, expires_utc, samesite "
            f"FROM cookies WHERE {host_clauses}"
        )

        cookies = []
        for row in cur:
            value = _decrypt(row[2])
            if not value:
                continue
            cookie: dict[str, Any] = {
                "name": row[1],
                "value": value,
                "domain": row[0],
                "path": row[3],
                "secure": bool(row[4]),
                "httpOnly": bool(row[5]),
            }
            if row[6] and row[6] > 0:
                # Chrome epoch: microseconds since 1601-01-01
                chrome_epoch_offset = 11644473600
                cookie["expires"] = (row[6] / 1_000_000) - chrome_epoch_offset
            samesite_map = {0: "None", 1: "Lax", 2: "Strict", -1: "None"}
            cookie["sameSite"] = samesite_map.get(row[7], "None")
            cookies.append(cookie)

        conn.close()
        logger.info("从 Chrome 提取了 %d 个 cookies", len(cookies))
        return cookies
    except Exception as e:
        logger.warning("Chrome cookie 提取失败: %s", e)
        return []
    finally:
        tmp_db.unlink(missing_ok=True)


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
        self._cookies_injected = False

    _OUR_CDP_PORT = 9333

    _PLATFORM_DOMAINS: dict[str, list[str]] = {
        "pdd": ["mms.pinduoduo.com", "pinduoduo.com"],
        "taobao": ["myseller.taobao.com", "seller.taobao.com"],
        "jd": ["shop.jd.com", "sz.jd.com"],
        "douyin": ["buyin.jinritemai.com", "fxg.jinritemai.com"],
    }

    async def _ensure_browser(self) -> None:
        """懒加载浏览器 — 多策略连接.

        优先级:
        1. CDP 连接已有 Chromium/Chrome (port 9333, 9222-9224)
        2. 启动 persistent context + 注入 Chrome cookies (如果检测到已登录)
        3. 启动 persistent context (干净状态)
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

        # Strategy 1: CDP — 先连我们的 Chromium (9333)，再连用户 Chrome (9222-9224)
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

        # Strategy 2 & 3: 启动 persistent context
        await self._launch_persistent_context()

    async def _launch_persistent_context(self) -> None:
        """启动 persistent context Chromium，带 CDP 端口供下次复用."""
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

    async def _try_inject_chrome_cookies(self, platform: str) -> bool:
        """检测用户 Chrome 是否已登录目标平台，如果是则注入 cookies."""
        if self._cookies_injected:
            return True
        if self._cdp_connected:
            return False

        domains = self._PLATFORM_DOMAINS.get(platform, [])
        if not domains:
            return False

        if not _chrome_has_platform_page(platform, domains):
            return False

        # Chrome 有目标页面 → 提取 cookies 注入
        cookies = _extract_chrome_cookies(domains)
        if not cookies:
            logger.info("Chrome 有 %s 页面但无法提取 cookies", platform)
            return False

        try:
            await self._context.add_cookies(cookies)
            self._cookies_injected = True
            logger.info("已注入 %d 个 Chrome cookies (%s)", len(cookies), platform)
            return True
        except Exception as e:
            logger.warning("Cookie 注入失败: %s", e)
            return False

    async def get_session(
        self, platform: str, account: str = "default",
    ) -> BrowserSession:
        """获取或创建浏览器会话."""
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

        # 非 CDP: 尝试注入 Chrome cookies (仅首次)
        await self._try_inject_chrome_cookies(platform)

        # 复用空白页 or 新开
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
