"""上下文引用 — 通过 @file / @url / @symbol 注入内容到对话.

用法:
    resolver = ContextReferenceResolver()
    expanded = await resolver.resolve("分析 @file:main.py 的性能")
    # → "分析 [以下是 main.py 的内容]\n...\n 的性能"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 匹配 @type:value 格式
_REF_PATTERN = re.compile(r"@(file|url|dir|symbol|memory):([^\s]+)")

@dataclass
class ResolvedReference:
    """解析后的引用."""

    ref_type: str
    ref_value: str
    content: str
    token_estimate: int = 0

class ContextReferenceResolver:
    """上下文引用解析器 — 将 @引用 展开为实际内容."""

    def __init__(self, workdir: Optional[str] = None, max_file_lines: int = 500) -> None:
        self.workdir = Path(workdir) if workdir else Path.cwd()
        self.max_file_lines = max_file_lines

    async def resolve(self, text: str) -> tuple[str, list[ResolvedReference]]:
        """解析文本中的所有 @引用，返回展开后的文本和引用列表."""
        refs: list[ResolvedReference] = []
        result = text

        for match in _REF_PATTERN.finditer(text):
            ref_type = match.group(1)
            ref_value = match.group(2)
            original = match.group(0)

            resolved = await self._resolve_single(ref_type, ref_value)
            if resolved:
                refs.append(resolved)
                result = result.replace(original, f"[{ref_type}: {ref_value}]", 1)

        # 附加引用内容
        if refs:
            result += "\n\n--- Attached Context ---\n"
            for ref in refs:
                result += f"\n### {ref.ref_type}: {ref.ref_value}\n```\n{ref.content}\n```\n"

        return result, refs

    async def _resolve_single(self, ref_type: str, ref_value: str) -> Optional[ResolvedReference]:
        """解析单个引用."""
        try:
            if ref_type == "file":
                return await self._resolve_file(ref_value)
            elif ref_type == "dir":
                return await self._resolve_dir(ref_value)
            elif ref_type == "url":
                return await self._resolve_url(ref_value)
            elif ref_type == "symbol":
                return await self._resolve_symbol(ref_value)
            elif ref_type == "memory":
                return await self._resolve_memory(ref_value)
        except Exception as e:
            logger.warning("解析引用 @%s:%s 失败: %s", ref_type, ref_value, e)
        return None

    async def _resolve_file(self, path: str) -> Optional[ResolvedReference]:
        """读取文件内容."""
        file_path = self.workdir / path
        if not file_path.exists():
            return None
        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")
        if len(lines) > self.max_file_lines:
            content = "\n".join(lines[: self.max_file_lines]) + f"\n... ({len(lines)} lines total)"
        return ResolvedReference(
            ref_type="file",
            ref_value=path,
            content=content,
            token_estimate=len(content) // 4,
        )

    async def _resolve_dir(self, path: str) -> Optional[ResolvedReference]:
        """列出目录内容."""
        dir_path = self.workdir / path
        if not dir_path.is_dir():
            return None
        entries = sorted(dir_path.iterdir())
        listing = "\n".join(
            f"{'📁 ' if e.is_dir() else '📄 '}{e.name}" for e in entries[:100]
        )
        return ResolvedReference(
            ref_type="dir", ref_value=path, content=listing, token_estimate=len(listing) // 4,
        )

    async def _resolve_url(self, url: str) -> Optional[ResolvedReference]:
        """抓取 URL 内容."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                content = resp.text[:10000]
                return ResolvedReference(
                    ref_type="url", ref_value=url, content=content,
                    token_estimate=len(content) // 4,
                )
        except Exception as e:
            logger.debug("URL fetch failed for '%s': %s", url, e)
            return None

    async def _resolve_symbol(self, symbol: str) -> Optional[ResolvedReference]:
        """搜索代码符号 (函数/类名)."""
        import re
        import subprocess

        if not re.match(r'^[\w.]+$', symbol):
            return None

        try:
            result = subprocess.run(
                ["grep", "-rn", symbol, str(self.workdir), "--include=*.py", "-l"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                files = result.stdout.strip().split("\n")[:10]
                content = f"Symbol '{symbol}' found in:\n" + "\n".join(files)
                return ResolvedReference(
                    ref_type="symbol", ref_value=symbol, content=content,
                    token_estimate=len(content) // 4,
                )
        except Exception as e:
            logger.debug("Symbol search failed for '%s': %s", symbol, e)
        return None

    async def _resolve_memory(self, query: str) -> Optional[ResolvedReference]:
        """搜索记忆 (占位，需要 MemoryManager 注入)."""
        return ResolvedReference(
            ref_type="memory", ref_value=query,
            content=f"(Memory search for '{query}' — requires MemoryManager integration)",
            token_estimate=20,
        )
