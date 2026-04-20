"""Web 相关工具集 — 网页搜索 + 网页获取 + 文件下载 + HTTP 请求 + API Mock.

从 extended.py 提取的 web 类工具。
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import os
import re
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_proxy() -> str | None:
    """读取代理配置: 环境变量 > config.yaml > macOS 系统代理 > None."""
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    try:
        from agent.core.config import Config
        cfg = Config.load()
        if getattr(cfg, "proxy", None):
            return cfg.proxy
    except Exception:
        pass
    # macOS: 读取系统代理设置
    try:
        import subprocess
        r = subprocess.run(
            ["scutil", "--proxy"], capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            out = r.stdout
            if "HTTPSEnable : 1" in out:
                host = re.search(r"HTTPSProxy\s*:\s*(\S+)", out)
                port = re.search(r"HTTPSPort\s*:\s*(\d+)", out)
                if host and port:
                    return f"http://{host.group(1)}:{port.group(1)}"
            if "HTTPEnable : 1" in out:
                host = re.search(r"HTTPProxy\s*:\s*(\S+)", out)
                port = re.search(r"HTTPPort\s*:\s*(\d+)", out)
                if host and port:
                    return f"http://{host.group(1)}:{port.group(1)}"
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════════════
#  Web Search — DuckDuckGo (需代理) → Sogou (国内直连) 自动 fallback
# ═══════════════════════════════════════════════════════════════════

_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


async def _search_duckduckgo(query: str, num_results: int, proxy: str | None) -> list[str]:
    """DuckDuckGo HTML 搜索 (需要代理或海外网络)."""
    import httpx

    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=10.0,
        proxy=proxy,
        headers={"User-Agent": _BROWSER_UA},
    ) as client:
        resp = await client.get(url)
        html = resp.text

    results: list[str] = []
    pattern = r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?<a class="result__snippet"[^>]*>(.*?)</a>'
    matches = re.findall(pattern, html, re.DOTALL)

    for href, title, snippet in matches[:num_results]:
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        if "uddg=" in href:
            parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = parsed.get("uddg", [href])[0]
        results.append(f"**{title}**\n  {href}\n  {snippet}")

    if not results:
        simple = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html)
        for href, title in simple[:num_results]:
            title = re.sub(r"<[^>]+>", "", title).strip()
            if title and "duckduckgo.com" not in href:
                results.append(f"**{title}**\n  {href}")

    return results


async def _search_sogou(query: str, num_results: int) -> list[str]:
    """搜狗搜索 (国内直连，无需代理)."""
    import httpx

    encoded_query = urllib.parse.quote_plus(query)
    url = f"https://www.sogou.com/web?query={encoded_query}"

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=15.0,
        headers={"User-Agent": _BROWSER_UA},
    ) as client:
        resp = await client.get(url)
        page = resp.text

    results: list[str] = []
    blocks = re.findall(
        r'<h3[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h3>(?:.*?<p[^>]*>(.*?)</p>)?',
        page, re.DOTALL,
    )

    for href, title, snippet in blocks:
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet or "").strip()
        href = html_mod.unescape(href)
        if not title or "sogou.com" in href or "yuanbao.tencent" in href:
            continue
        if href.startswith("/"):
            href = "https://www.sogou.com" + href
        entry = f"**{title}**\n  {href}"
        if snippet:
            entry += f"\n  {snippet[:200]}"
        results.append(entry)
        if len(results) >= num_results:
            break

    return results


async def web_search(
    query: str,
    num_results: int = 5,
    engine: str = "auto",
) -> str:
    """搜索网页.

    Args:
        query: 搜索查询
        num_results: 返回结果数 (默认 5)
        engine: 搜索引擎 ("auto" | "duckduckgo" | "sogou")

    Returns:
        搜索结果 (标题 + URL + 摘要)
    """
    proxy = _get_proxy()

    if engine == "sogou":
        try:
            results = await _search_sogou(query, num_results)
            if results:
                return f"搜索结果 ({len(results)} 条):\n\n" + "\n\n".join(results)
        except Exception as e:
            return f"搜索失败 (sogou): {e}"

    if engine == "duckduckgo":
        try:
            results = await _search_duckduckgo(query, num_results, proxy)
            if results:
                return f"搜索结果 ({len(results)} 条):\n\n" + "\n\n".join(results)
        except Exception as e:
            return f"搜索失败 (duckduckgo): {e}"

    # auto: DuckDuckGo 优先 → Sogou fallback
    ddg_error = ""
    try:
        results = await _search_duckduckgo(query, num_results, proxy)
        if results:
            return f"搜索结果 ({len(results)} 条):\n\n" + "\n\n".join(results)
    except Exception as e:
        ddg_error = str(e)
        logger.debug("DuckDuckGo 搜索失败，切换到搜狗: %s", e)

    try:
        results = await _search_sogou(query, num_results)
        if results:
            return f"搜索结果 ({len(results)} 条):\n\n" + "\n\n".join(results)
    except Exception as e:
        logger.warning("搜狗搜索也失败: %s", e)
        return f"搜索失败: DuckDuckGo ({ddg_error}), Sogou ({e})"

    return f"搜索 '{query}' 未找到结果。"

async def web_fetch(
    url: str,
    extract: str = "text",
    max_length: int = 8000,
) -> str:
    """获取网页内容.

    Args:
        url: 网页 URL
        extract: 提取模式 ("text" | "html" | "markdown")
        max_length: 最大内容长度

    Returns:
        网页内容
    """
    try:
        import httpx

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            headers={"User-Agent": "Mozilla/5.0 (compatible; XJDAgent/1.0)"},
        ) as client:
            resp = await client.get(url)
            content_type = resp.headers.get("content-type", "")

            if "json" in content_type:
                try:
                    data = resp.json()
                    text = json.dumps(data, ensure_ascii=False, indent=2)
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.debug("JSON decode failed, falling back to text: %s", exc)
                    text = resp.text
            else:
                text = resp.text

            # 简单 HTML → 文本转换
            if extract == "text" and "html" in content_type:
                # 去掉 script / style
                text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
                text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
                # 去掉所有 HTML 标签
                text = re.sub(r"<[^>]+>", " ", text)
                # 合并空白
                text = re.sub(r"\s+", " ", text).strip()

            # 截断
            if len(text) > max_length:
                text = text[:max_length] + f"\n...(截断, 共 {len(text)} 字符)"

            return f"[{url}]\n{text}"

    except Exception as e:
        return f"获取网页失败: {e}"

# ═══════════════════════════════════════════════════════════════════
#  Download File — 下载文件
# ═══════════════════════════════════════════════════════════════════

async def download_file(
    url: str,
    save_path: str = "",
) -> str:
    """下载文件到本地.

    Args:
        url: 文件 URL
        save_path: 保存路径 (默认保存到 ~/Downloads/)

    Returns:
        下载结果
    """
    try:
        import httpx

        if not save_path:
            filename = url.split("/")[-1].split("?")[0] or "download"
            save_path = str(Path.home() / "Downloads" / filename)

        p = Path(save_path).expanduser().resolve()
        p.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(follow_redirects=True, timeout=120.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            with open(p, "wb") as f:
                f.write(resp.content)

            size = len(resp.content)
            if size < 1024:
                size_str = f"{size}B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f}KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f}MB"

            return f"已下载: {p} ({size_str})"

    except Exception as e:
        return f"下载失败: {e}"

# ═══════════════════════════════════════════════════════════════════
#  HTTP Request — 发送 HTTP 请求
# ═══════════════════════════════════════════════════════════════════

async def _http_request(method: str, url: str, headers: str = "", body: str = "", **kwargs) -> str:
    """发送 HTTP 请求."""
    try:
        import httpx
        h = json.loads(headers) if headers else {}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.request(method, url, headers=h, content=body or None)
            output = f"HTTP {resp.status_code}\n"
            output += "\n".join(f"{k}: {v}" for k, v in resp.headers.items()) + "\n\n"
            output += resp.text
            if len(output) > 20000:
                output = output[:20000] + "\n... (截断)"
            return output
    except Exception as e:
        return f"请求失败: {e}"

# ═══════════════════════════════════════════════════════════════════
#  API Mock — 简易 HTTP mock 服务器
# ═══════════════════════════════════════════════════════════════════

async def _api_mock(routes: str, port: int = 0, timeout: int = 60, **kw) -> str:
    """启动简易 HTTP mock 服务器."""
    try:
        from aiohttp import web
        route_list = json.loads(routes)
        app = web.Application()

        for r in route_list:
            method = r.get("method", "GET").upper()
            path = r.get("path", "/")
            status = r.get("status", 200)
            body = r.get("body", "")

            async def handler(req, _s=status, _b=body):
                return web.Response(text=_b, status=_s)

            app.router.add_route(method, path, handler)

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port)
        await site.start()
        actual_port = site._server.sockets[0].getsockname()[1]

        # 自动关闭
        async def auto_shutdown():
            await asyncio.sleep(timeout)
            await runner.cleanup()

        asyncio.create_task(auto_shutdown())
        return f"Mock 服务器已启动: http://127.0.0.1:{actual_port} (自动关闭: {timeout}s)"
    except Exception as e:
        return f"Mock 服务器启动失败: {e}"


# ═══════════════════════════════════════════════════════════════════
#  注册所有 Web 工具
# ═══════════════════════════════════════════════════════════════════

def register_web_tools(registry: "ToolRegistry") -> None:
    """注册所有 web 相关工具."""

    registry.register(
        name="web_search",
        description="搜索互联网，获取最新信息。自动选择可用搜索引擎 (有代理用 DuckDuckGo，无代理用搜狗)。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索查询"},
                "num_results": {"type": "integer", "description": "返回结果数 (默认 5)", "default": 5},
                "engine": {"type": "string", "description": "搜索引擎 (auto/duckduckgo/sogou)", "default": "auto"},
            },
            "required": ["query"],
        },
        handler=web_search,
        category="web",
    )

    registry.register(
        name="web_fetch",
        description="获取指定 URL 的网页内容，提取文本。",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "网页 URL"},
                "extract": {"type": "string", "description": "提取模式: text|html", "default": "text"},
                "max_length": {"type": "integer", "description": "最大内容长度", "default": 8000},
            },
            "required": ["url"],
        },
        handler=web_fetch,
        category="web",
    )

    registry.register(
        name="download_file",
        description="从 URL 下载文件到本地。",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "文件 URL"},
                "save_path": {"type": "string", "description": "保存路径 (默认 ~/Downloads/)", "default": ""},
            },
            "required": ["url"],
        },
        handler=download_file,
        category="web",
    )

    registry.register(
        name="http_request",
        description="发送 HTTP 请求 (GET/POST/PUT/DELETE)。",
        parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "description": "HTTP 方法", "enum": ["GET", "POST", "PUT", "DELETE"]},
                "url": {"type": "string", "description": "请求 URL"},
                "headers": {"type": "string", "description": "请求头 JSON"},
                "body": {"type": "string", "description": "请求体"},
            },
            "required": ["method", "url"],
        },
        handler=_http_request,
        category="web",
        requires_approval=True,
    )

    registry.register(
        name="api_mock",
        description="启动简易 HTTP mock 服务器。",
        parameters={
            "type": "object",
            "properties": {
                "routes": {"type": "string", "description": "路由配置 JSON 数组 [{method, path, status, body}]"},
                "port": {"type": "integer", "description": "端口 (0=随机)", "default": 0},
                "timeout": {"type": "integer", "description": "自动关闭秒数", "default": 60},
            },
            "required": ["routes"],
        },
        handler=_api_mock,
        category="web",
        requires_approval=True,
        optional_deps=["aiohttp"],
    )
