"""Workspace 文件管理 — 统一临时文件、文件访问安全校验."""

from __future__ import annotations

from pathlib import Path

from .config import get_workspace_dir, get_tmp_dir

ALLOWED_ROOTS: list[Path] = []


def workspace_tmp(suffix: str = "", prefix: str = "xjd_") -> Path:
    import uuid
    name = f"{prefix}{uuid.uuid4().hex[:8]}{suffix}"
    return get_tmp_dir() / name


def resolve_safe_path(raw: str) -> Path | None:
    try:
        p = Path(raw).expanduser().resolve()
    except Exception:
        return None
    if not p.exists() or not p.is_file():
        return None
    ws = get_workspace_dir().resolve()
    if p == ws or p.is_relative_to(ws):
        return p
    home = Path.home().resolve()
    if p.is_relative_to(home):
        return p
    for root in ALLOWED_ROOTS:
        r = root.resolve()
        if p.is_relative_to(r):
            return p
    return None


def cleanup_tmp(max_age_hours: int = 24) -> int:
    import time
    tmp = get_tmp_dir()
    if not tmp.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    count = 0
    for f in tmp.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            count += 1
    return count
