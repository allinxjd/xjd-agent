"""CodeManager — 项目代码索引与访问.

将 company.py 中散落的 _collect_project_files、_extract_project_description、
_extract_tech_stack 统一为 CodeManager，提供结构化的代码访问接口。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules",
    ".git", ".tox", ".mypy_cache", ".ruff_cache", "dist", "build",
    ".egg-info", ".eggs",
}
_SKIP_EXT = {
    ".pyc", ".class", ".o", ".so", ".db", ".sqlite",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".lock", ".min.js", ".min.css", ".map",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".exe", ".dll", ".dylib", ".bin",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
}
_MAX_FILE_SIZE = 50 * 1024


@dataclass
class FileEntry:
    """单个代码文件的索引条目."""
    rel_path: str
    size: int
    language: str = ""

    @property
    def extension(self) -> str:
        return Path(self.rel_path).suffix


class CodeManager:
    """管理项目代码文件的索引和访问."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._src_dir = self._detect_src_dir()
        self._index: list[FileEntry] = []
        self._tech_stack: str = ""
        self._description: str = ""
        self._indexed = False

    @property
    def project_dir(self) -> Path:
        return self._project_dir

    @property
    def src_dir(self) -> Path:
        return self._src_dir

    @property
    def file_count(self) -> int:
        self._ensure_indexed()
        return len(self._index)

    @property
    def tech_stack(self) -> str:
        if not self._tech_stack:
            self._tech_stack = self._detect_tech_stack()
        return self._tech_stack

    @property
    def description(self) -> str:
        if not self._description:
            self._description = self._detect_description()
        return self._description

    @property
    def has_code(self) -> bool:
        """项目是否已有代码文件."""
        self._ensure_indexed()
        return self._index_has_code()

    def get_index(self) -> list[FileEntry]:
        """获取文件索引."""
        self._ensure_indexed()
        return list(self._index)

    def get_file_tree(self, max_files: int = 50) -> str:
        """生成文件树摘要."""
        self._ensure_indexed()
        if not self._index:
            return ""
        lines = []
        for entry in self._index[:max_files]:
            lines.append(f"  {entry.rel_path} ({entry.size}B)")
        result = f"项目文件 ({len(self._index)} 个):\n" + "\n".join(lines)
        if len(self._index) > max_files:
            result += f"\n  ... 还有 {len(self._index) - max_files} 个文件"
        return result

    def invalidate(self) -> None:
        """代码变更后重建索引."""
        self._indexed = False
        self._index.clear()
        self._tech_stack = ""

    def get_context_for_stage(self, stage: str, max_chars: int = 5000) -> str:
        """根据阶段返回适当的代码上下文."""
        if stage in ("WritePRD", "WritePrototype", "WriteUIDesign"):
            return self.tech_stack
        elif stage == "WriteDesign":
            return self.tech_stack + "\n" + self.get_file_tree()
        elif stage in ("WriteCode", "FixCode"):
            return self.collect_files(max_chars=max_chars, max_files=20)
        elif stage in ("CodeReview", "WriteTest"):
            return self.collect_files(max_chars=max_chars, max_files=50)
        return self.tech_stack

    # ── Private ──

    def _detect_src_dir(self) -> Path:
        src = self._project_dir / "src"
        if src.exists() and any(f.is_file() for f in src.rglob("*")):
            return src
        return self._project_dir

    def _ensure_indexed(self) -> None:
        if self._indexed:
            return
        self._index = self._scan_files()
        self._indexed = True

    def _scan_files(self) -> list[FileEntry]:
        entries = []
        scan_dir = self._src_dir
        for f in sorted(scan_dir.rglob("*")):
            if not f.is_file():
                continue
            try:
                rel = f.relative_to(self._project_dir)
            except ValueError:
                continue
            if any(part in _SKIP_DIRS for part in rel.parts):
                continue
            if f.suffix in _SKIP_EXT:
                continue
            if f.name.startswith("."):
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if size > _MAX_FILE_SIZE or size == 0:
                continue
            try:
                head = f.read_bytes()[:512]
                if b"\x00" in head:
                    continue
            except Exception:
                continue
            lang = self._detect_language(f.suffix)
            entries.append(FileEntry(rel_path=str(rel), size=size, language=lang))
        return entries

    def _index_has_code(self) -> bool:
        code_ext = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java"}
        return any(Path(e.rel_path).suffix in code_ext for e in self._index)

    @staticmethod
    def _detect_language(ext: str) -> str:
        lang_map = {
            ".py": "python", ".js": "javascript", ".ts": "typescript",
            ".tsx": "typescript", ".jsx": "javascript", ".go": "go",
            ".rs": "rust", ".java": "java", ".html": "html",
            ".css": "css", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".toml": "toml", ".md": "markdown", ".sh": "shell",
        }
        return lang_map.get(ext, "")

    def _detect_tech_stack(self) -> str:
        from agent.company.company import Company
        return Company._extract_tech_stack(self._project_dir)

    def _detect_description(self) -> str:
        from agent.company.company import Company
        return Company._extract_project_description(self._project_dir)

    def collect_files(self, max_chars: int = 30000, max_files: int = 100) -> str:
        """收集项目代码文件内容（兼容原 _collect_project_files 接口）."""
        self._ensure_indexed()
        files_content = []
        total = 0
        file_count = 0
        for entry in self._index:
            if file_count >= max_files:
                files_content.append(f"\n... (已达文件上限 {max_files})")
                break
            fpath = self._project_dir / entry.rel_path
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            file_count += 1
            block = f"\n### {entry.rel_path}\n```\n{text}\n```\n"
            if total + len(block) > max_chars:
                remaining = max_chars - total
                if remaining > 200:
                    truncated = text[:remaining - 100]
                    block = f"\n### {entry.rel_path}\n```\n{truncated}\n... (截断)\n```\n"
                    files_content.append(block)
                files_content.append(f"\n... (更多文件省略)")
                break
            files_content.append(block)
            total += len(block)
        return "".join(files_content) if files_content else ""
