"""File operation tools: diff, archive, compress, regex replace."""

import difflib
import json
import logging
import os
import re
import tarfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def _diff_files(file_a: str, file_b: str, **kwargs) -> str:
    """比较两个文件."""
    try:
        a = Path(file_a).expanduser().read_text(encoding="utf-8").splitlines()
        b = Path(file_b).expanduser().read_text(encoding="utf-8").splitlines()
        diff = list(difflib.unified_diff(a, b, fromfile=file_a, tofile=file_b, lineterm=""))
        if not diff:
            return "两个文件内容相同"
        output = "\n".join(diff)
        if len(output) > 20000:
            output = output[:20000] + "\n... (截断)"
        return output
    except Exception as e:
        return f"比较失败: {e}"


async def _archive_extract(archive_path: str, dest_path: str = ".", **kwargs) -> str:
    """解压文件."""
    try:
        p = Path(archive_path).expanduser().resolve()
        dest = Path(dest_path).expanduser().resolve()
        dest.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".zip":
            with zipfile.ZipFile(p) as zf:
                zf.extractall(dest)
                return f"已解压 {len(zf.namelist())} 个文件到 {dest}"
        elif p.name.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar")):
            with tarfile.open(p) as tf:
                tf.extractall(dest, filter="data")
                return f"已解压到 {dest}"
        else:
            return f"不支持的格式: {p.suffix}"
    except Exception as e:
        return f"解压失败: {e}"


async def _regex_replace(file_path: str, pattern: str, replacement: str, flags: str = "", **kw) -> str:
    """正则替换文件内容."""
    try:
        p = Path(file_path)
        if not p.exists():
            return f"文件不存在: {file_path}"
        content = p.read_text(encoding="utf-8")
        re_flags = 0
        if "i" in flags:
            re_flags |= re.IGNORECASE
        if "m" in flags:
            re_flags |= re.MULTILINE
        new_content, count = re.subn(pattern, replacement, content, flags=re_flags)
        p.write_text(new_content, encoding="utf-8")
        return f"替换完成, 共 {count} 处"
    except Exception as e:
        return f"正则替换失败: {e}"


async def _file_compress(paths: str, output: str, fmt: str = "zip", **kw) -> str:
    """创建压缩包."""
    try:
        file_list = json.loads(paths) if paths.startswith("[") else [paths]
        if fmt == "zip":
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in file_list:
                    p = Path(fp)
                    if p.is_dir():
                        for f in p.rglob("*"):
                            if f.is_file():
                                zf.write(f, f.relative_to(p.parent))
                    elif p.is_file():
                        zf.write(p, p.name)
        elif fmt in ("tar.gz", "tgz"):
            with tarfile.open(output, "w:gz") as tf:
                for fp in file_list:
                    tf.add(fp, arcname=Path(fp).name)
        else:
            return f"不支持的格式: {fmt}"
        return f"已创建: {output}"
    except Exception as e:
        return f"压缩失败: {e}"


def register_file_tools(registry):
    """Register file operation tools."""

    registry.register(
        name="diff_files",
        description="比较两个文件的差异，输出 unified diff。",
        parameters={
            "type": "object",
            "properties": {
                "file_a": {"type": "string", "description": "第一个文件路径"},
                "file_b": {"type": "string", "description": "第二个文件路径"},
            },
            "required": ["file_a", "file_b"],
        },
        handler=_diff_files,
        category="file",
    )

    registry.register(
        name="archive_extract",
        description="解压 zip/tar/tar.gz 文件。",
        parameters={
            "type": "object",
            "properties": {
                "archive_path": {"type": "string", "description": "压缩文件路径"},
                "dest_path": {"type": "string", "description": "解压目标目录", "default": "."},
            },
            "required": ["archive_path"],
        },
        handler=_archive_extract,
        category="file",
        requires_approval=True,
    )

    registry.register(
        name="regex_replace",
        description="正则表达式替换文件内容。",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径"},
                "pattern": {"type": "string", "description": "正则表达式"},
                "replacement": {"type": "string", "description": "替换文本"},
                "flags": {"type": "string", "description": "标志 (i=忽略大小写, m=多行)", "default": ""},
            },
            "required": ["file_path", "pattern", "replacement"],
        },
        handler=_regex_replace,
        category="file",
        requires_approval=True,
    )

    registry.register(
        name="file_compress",
        description="创建压缩包 (zip/tar.gz)。",
        parameters={
            "type": "object",
            "properties": {
                "paths": {"type": "string", "description": "文件/目录路径 (JSON 数组或单个路径)"},
                "output": {"type": "string", "description": "输出文件路径"},
                "fmt": {"type": "string", "description": "格式: zip 或 tar.gz", "default": "zip"},
            },
            "required": ["paths", "output"],
        },
        handler=_file_compress,
        category="file",
    )
