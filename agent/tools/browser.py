"""浏览器自动化工具 — Playwright 异步 API.

支持 8 种操作: navigate, click, type, screenshot, extract_text, evaluate_js, wait_for_selector, upload_file.
Playwright 为可选依赖，首次调用时懒加载。

用法:
    # 在 register_extended_tools 中注册
    from agent.tools.browser import register_browser_tools
    register_browser_tools(registry)
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from agent.core.workspace_files import workspace_tmp
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 懒加载单例
_playwright = None
_browser = None
_context = None
_page = None
_stealth_mode = False

# ── 反检测 JS 注入脚本 ──────────────────────────────────────────
STEALTH_SCRIPTS = [
    # 隐藏 webdriver 标志
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
    # 伪造 plugins
    """Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5].map(() => ({
            name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer',
            description: 'Portable Document Format',
        })),
    })""",
    # 伪造 languages
    "Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en-US', 'en']})",
    # 隐藏 chrome.runtime
    "window.chrome = {runtime: {}, loadTimes: () => ({}), csi: () => ({})}",
    # 伪造 permissions
    """const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) =>
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : originalQuery(parameters)""",
    # WebGL vendor/renderer
    """const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';
        return getParameter.call(this, parameter);
    }""",
]

async def _get_page(stealth: bool = False, cdp_url: str = ""):
    """获取或创建浏览器页面 (懒加载单例).

    Args:
        stealth: 启用反检测模式
        cdp_url: Chrome DevTools Protocol URL (如 http://localhost:9222)
                 非空时连接已运行的 Chrome 实例而非启动新浏览器
    """
    global _playwright, _browser, _context, _page, _stealth_mode

    if _page and not _page.is_closed():
        return _page

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError(
            "playwright 未安装。请运行:\n"
            "  pip install 'xjd-agent[browser]'\n"
            "  playwright install chromium"
        )

    if not _playwright:
        _playwright = await async_playwright().start()

        if cdp_url:
            # CDP Live Attach — 连接已运行的 Chrome 实例
            _browser = await _playwright.chromium.connect_over_cdp(cdp_url)
            contexts = _browser.contexts
            if contexts:
                _context = contexts[0]
                pages = _context.pages
                if pages:
                    _page = pages[0]
                    return _page
            _context = _context or await _browser.new_context()
        else:
            launch_args = ["--disable-blink-features=AutomationControlled"] if stealth else []
            _browser = await _playwright.chromium.launch(headless=True, args=launch_args)

            context_opts = {
                "viewport": {"width": 1280, "height": 720},
                "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
            }
            if stealth:
                context_opts["locale"] = "zh-CN"
                context_opts["timezone_id"] = "Asia/Shanghai"

            _context = await _browser.new_context(**context_opts)

            # 注入反检测脚本
            if stealth:
                _stealth_mode = True
                for script in STEALTH_SCRIPTS:
                    await _context.add_init_script(script)

    _page = await _context.new_page()
    return _page

async def _browser_action(
    action: str,
    url: str = "",
    selector: str = "",
    text: str = "",
    script: str = "",
    timeout: int = 30000,
    stealth: bool = False,
    cdp_url: str = "",
    **kwargs,
) -> str:
    """浏览器操作入口."""
    page = await _get_page(stealth=stealth, cdp_url=cdp_url)
    MAX_OUTPUT = 20000

    if action == "navigate":
        if not url:
            return "错误: navigate 需要 url 参数"
        resp = await page.goto(url, timeout=timeout, wait_until="domcontentloaded")
        title = await page.title()
        return f"已导航到: {url}\n标题: {title}\n状态码: {resp.status if resp else 'unknown'}"

    elif action == "click":
        if not selector:
            return "错误: click 需要 selector 参数"
        await page.click(selector, timeout=timeout)
        return f"已点击: {selector}"

    elif action == "type":
        if not selector or not text:
            return "错误: type 需要 selector 和 text 参数"
        await page.fill(selector, text, timeout=timeout)
        return f"已输入 '{text[:50]}' 到 {selector}"

    elif action == "screenshot":
        path = str(workspace_tmp(".png", "browser_"))
        await page.screenshot(path=path, full_page=bool(kwargs.get("full_page")))
        return f"截图已保存: {path}"

    elif action == "extract_text":
        if selector:
            el = await page.query_selector(selector)
            if not el:
                return f"未找到元素: {selector}"
            content = await el.text_content() or ""
        else:
            content = await page.content()
            # 简单提取文本
            import re
            content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()
        if len(content) > MAX_OUTPUT:
            content = content[:MAX_OUTPUT] + f"\n... (截断，共 {len(content)} 字符)"
        return content

    elif action == "evaluate_js":
        if not script:
            return "错误: evaluate_js 需要 script 参数"
        result = await page.evaluate(script)
        output = str(result)
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "... (截断)"
        return output

    elif action == "wait_for_selector":
        if not selector:
            return "错误: wait_for_selector 需要 selector 参数"
        await page.wait_for_selector(selector, timeout=timeout)
        return f"元素已出现: {selector}"

    elif action == "upload_file":
        file_path = kwargs.get("file_path", "")
        if not selector or not file_path:
            return "错误: upload_file 需要 selector (文件输入框) 和 file_path (本地文件路径) 参数"
        from pathlib import Path
        fp = Path(file_path).expanduser()
        if not fp.exists():
            return f"错误: 文件不存在: {fp}"
        await page.set_input_files(selector, str(fp), timeout=timeout)
        return f"已上传文件: {fp.name} → {selector}"

    else:
        return f"未知操作: {action}。支持: navigate, click, type, screenshot, extract_text, evaluate_js, wait_for_selector, upload_file"

async def close_browser() -> None:
    """关闭浏览器 (清理资源)."""
    global _playwright, _browser, _context, _page
    if _browser:
        await _browser.close()
    if _playwright:
        await _playwright.stop()
    _playwright = _browser = _context = _page = None

def register_browser_tools(registry) -> None:
    """注册浏览器工具到 ToolRegistry."""
    registry.register(
        name="browser_action",
        description=(
            "浏览器自动化操作。支持 8 种 action:\n"
            "- navigate: 导航到 URL\n"
            "- click: 点击元素 (CSS selector)\n"
            "- type: 在输入框中输入文本\n"
            "- screenshot: 截取页面截图\n"
            "- extract_text: 提取页面或元素文本\n"
            "- evaluate_js: 执行 JavaScript\n"
            "- wait_for_selector: 等待元素出现\n"
            "- upload_file: 上传本地文件到 file input 元素"
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["navigate", "click", "type", "screenshot",
                             "extract_text", "evaluate_js", "wait_for_selector", "upload_file"],
                },
                "url": {"type": "string", "description": "目标 URL (navigate 时必填)"},
                "selector": {"type": "string", "description": "CSS 选择器"},
                "text": {"type": "string", "description": "输入文本 (type 时必填)"},
                "script": {"type": "string", "description": "JavaScript 代码 (evaluate_js 时必填)"},
                "file_path": {"type": "string", "description": "本地文件路径 (upload_file 时必填)"},
                "timeout": {"type": "integer", "description": "超时毫秒数", "default": 30000},
                "stealth": {"type": "boolean", "description": "启用反检测模式 (隐藏自动化特征)", "default": False},
            },
            "required": ["action"],
        },
        handler=_browser_action,
        category="browser",
        requires_approval=True,
    )
