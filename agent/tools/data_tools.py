"""数据处理工具集 — 数据库查询 + JSON 查询 + 模板渲染 + 文本变换 + PDF 提取.

从 extended.py 中提取的数据相关工具。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import subprocess
import urllib.parse
from pathlib import Path

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
#  Handler functions
# ═══════════════════════════════════════════════════════════════════

async def _database_query(db_path: str, query: str, params: str = "[]", **kw) -> str:
    """执行 SQLite 查询."""
    try:
        import aiosqlite
        parsed_params = json.loads(params) if params else []
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, parsed_params)
            if query.strip().upper().startswith("SELECT"):
                rows = await cursor.fetchall()
                if not rows:
                    return "查询结果: 0 行"
                cols = [d[0] for d in cursor.description]
                result = [dict(zip(cols, row)) for row in rows]
                return json.dumps(result, ensure_ascii=False, indent=2, default=str)[:10000]
            else:
                await db.commit()
                return f"执行成功, 影响 {cursor.rowcount} 行"
    except Exception as e:
        return f"数据库查询失败: {e}"


async def _json_query(query: str, path: str = "", data: str = "", **kwargs) -> str:
    """查询 JSON 数据."""
    try:
        if path:
            raw = Path(path).expanduser().read_text(encoding="utf-8")
            obj = json.loads(raw)
        elif data:
            obj = json.loads(data)
        else:
            return "错误: 需要 path 或 data 参数"
        for key in query.split("."):
            if isinstance(obj, list):
                obj = obj[int(key)]
            elif isinstance(obj, dict):
                obj = obj[key]
            else:
                return f"无法在 {type(obj).__name__} 上查询 '{key}'"
        return json.dumps(obj, ensure_ascii=False, indent=2) if isinstance(obj, (dict, list)) else str(obj)
    except Exception as e:
        return f"查询失败: {e}"


async def _template_render(template: str, variables: str = "{}", **kw) -> str:
    """Jinja2 模板渲染."""
    try:
        from jinja2 import Environment
        env = Environment()
        tmpl = env.from_string(template)
        ctx = json.loads(variables) if isinstance(variables, str) else variables
        return tmpl.render(**ctx)
    except Exception as e:
        return f"模板渲染失败: {e}"


async def _text_transform(text: str, operation: str, **kw) -> str:
    """文本变换 (base64/url/hash)."""
    ops = {
        "base64_encode": lambda t: base64.b64encode(t.encode()).decode(),
        "base64_decode": lambda t: base64.b64decode(t.encode()).decode(),
        "url_encode": lambda t: urllib.parse.quote(t),
        "url_decode": lambda t: urllib.parse.unquote(t),
        "md5": lambda t: hashlib.md5(t.encode()).hexdigest(),
        "sha256": lambda t: hashlib.sha256(t.encode()).hexdigest(),
        "sha1": lambda t: hashlib.sha1(t.encode()).hexdigest(),
        "upper": lambda t: t.upper(),
        "lower": lambda t: t.lower(),
    }
    fn = ops.get(operation)
    if not fn:
        return f"不支持的操作: {operation}, 可选: {', '.join(ops)}"
    try:
        return fn(text)
    except Exception as e:
        return f"变换失败: {e}"


async def _pdf_extract(path: str, pages: str = "", **kwargs) -> str:
    """提取 PDF 文本."""
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            if pages:
                parts = pages.split("-")
                start = int(parts[0]) - 1
                end = int(parts[1]) if len(parts) > 1 else start + 1
                target = pdf.pages[start:end]
            else:
                target = pdf.pages
            text = "\n\n".join(p.extract_text() or "" for p in target)
            if len(text) > 20000:
                text = text[:20000] + "\n... (截断)"
            return text or "(PDF 无可提取文本)"
    except ImportError:
        try:
            r = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True, timeout=30)
            return r.stdout[:20000] if r.stdout else "(无文本)"
        except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
            logger.debug("pdftotext fallback failed: %s", exc)
            return "错误: pdfplumber 未安装且 pdftotext 不可用。请运行: pip install pdfplumber"
    except Exception as e:
        return f"PDF 提取失败: {e}"


# ═══════════════════════════════════════════════════════════════════
#  Registration
# ═══════════════════════════════════════════════════════════════════

def register_data_tools(registry) -> None:
    """注册所有数据处理工具."""

    registry.register(
        name="database_query",
        description="执行 SQLite 数据库查询。",
        parameters={
            "type": "object",
            "properties": {
                "db_path": {"type": "string", "description": "SQLite 数据库文件路径"},
                "query": {"type": "string", "description": "SQL 查询语句"},
                "params": {"type": "string", "description": "查询参数 JSON 数组", "default": "[]"},
            },
            "required": ["db_path", "query"],
        },
        handler=_database_query,
        category="data",
        requires_approval=True,
        optional_deps=["aiosqlite"],
    )

    registry.register(
        name="json_query",
        description="查询 JSON 数据。支持点路径 (如 data.items.0.name) 或读取 JSON 文件。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "JSON 文件路径 (可选)"},
                "data": {"type": "string", "description": "JSON 字符串 (可选，与 path 二选一)"},
                "query": {"type": "string", "description": "点路径查询 (如 data.items.0.name)"},
            },
            "required": ["query"],
        },
        handler=_json_query,
        category="data",
    )

    registry.register(
        name="template_render",
        description="Jinja2 模板渲染。",
        parameters={
            "type": "object",
            "properties": {
                "template": {"type": "string", "description": "Jinja2 模板字符串"},
                "variables": {"type": "string", "description": "变量 JSON 对象", "default": "{}"},
            },
            "required": ["template"],
        },
        handler=_template_render,
        category="data",
        optional_deps=["jinja2"],
    )

    registry.register(
        name="text_transform",
        description="文本变换: base64/url 编解码、MD5/SHA 哈希、大小写转换。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "输入文本"},
                "operation": {"type": "string", "description": "操作", "enum": [
                    "base64_encode", "base64_decode", "url_encode", "url_decode",
                    "md5", "sha256", "sha1", "upper", "lower",
                ]},
            },
            "required": ["text", "operation"],
        },
        handler=_text_transform,
        category="data",
    )

    registry.register(
        name="pdf_extract",
        description="从 PDF 文件中提取文本。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "PDF 文件路径"},
                "pages": {"type": "string", "description": "页码范围 (如 1-5)，留空提取全部"},
            },
            "required": ["path"],
        },
        handler=_pdf_extract,
        category="file",
        optional_deps=["pdfplumber"],
    )
