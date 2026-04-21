"""自动更新 — 版本检查 + 一键升级.

支持两种更新源:
1. PyPI (pip install --upgrade)
2. Git (git pull + pip install -e .)

用法:
    from agent.core.updater import check_latest_version, auto_update

    latest = await check_latest_version()
    if latest and latest != get_current_version():
        await auto_update()
"""

from __future__ import annotations

import logging
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# 项目信息
PACKAGE_NAME = "xjd-agent"
GITHUB_REPO = "allinxjd/xjd-agent"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
GITHUB_TAGS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/tags"

def get_current_version() -> str:
    """获取当前安装版本."""
    try:
        from importlib.metadata import version
        return version(PACKAGE_NAME)
    except Exception:
        logger.debug("importlib.metadata version lookup failed")
    try:
        from pathlib import Path
        import re
        pyproject = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject.exists():
            text = pyproject.read_text()
            m = re.search(r'version\s*=\s*"([^"]+)"', text)
            if m:
                return m.group(1)
    except Exception:
        logger.debug("pyproject.toml version lookup failed")

def compare_versions(current: str, latest: str) -> bool:
    """比较版本号，返回 True 表示有更新.

    支持语义版本: 1.2.3, v1.2.3
    """
    def parse(v: str) -> tuple[int, ...]:
        v = v.strip().lstrip("v")
        parts = []
        for p in v.split(".")[:3]:
            try:
                parts.append(int(p.split("-")[0].split("+")[0]))
            except ValueError:
                parts.append(0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts)

    return parse(latest) > parse(current)

async def check_latest_version() -> Optional[str]:
    """检查最新版本 (PyPI + GitHub).

    Returns:
        最新版本号，如果无法检查则返回 None
    """
    # 1. 检查 PyPI
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(PYPI_JSON_URL)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("info", {}).get("version")
    except Exception as e:
        logger.debug("PyPI check failed: %s", e)

    # 2. 检查 GitHub Tags
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                GITHUB_TAGS_URL,
                headers={"Accept": "application/vnd.github.v3+json"},
            )
            if resp.status_code == 200:
                tags = resp.json()
                if tags:
                    tag = tags[0].get("name", "")
                    return tag.lstrip("v") if tag else None
    except Exception as e:
        logger.debug("GitHub tags check failed: %s", e)

    # 3. 检查 git remote (git clone 用户)
    try:
        from pathlib import Path
        repo_dir = Path(__file__).parent.parent.parent
        if not (repo_dir / ".git").exists():
            return None
        subprocess.run(
            ["git", "fetch", "--tags", "origin"],
            capture_output=True, text=True, timeout=30,
            cwd=str(repo_dir),
        )
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0", "origin/main"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_dir),
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lstrip("v")
    except Exception as e:
        logger.debug("Git check failed: %s", e)

    return None

async def auto_update(method: str = "auto") -> bool:
    """执行自动更新.

    Args:
        method: "pip" | "git" | "auto"

    Returns:
        是否更新成功
    """
    if method == "auto":
        # 检测是否在 git 仓库中
        from pathlib import Path
        repo_dir = Path(__file__).parent.parent.parent
        if (repo_dir / ".git").exists():
            method = "git"
        else:
            method = "pip"

    if method == "pip":
        return _update_pip()
    elif method == "git":
        return _update_git()
    return False

def _update_pip() -> bool:
    """通过 pip 更新."""
    try:
        result = subprocess.run(
            ["pip", "install", "--upgrade", PACKAGE_NAME],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            logger.info("pip upgrade succeeded")
            return True
        logger.warning("pip upgrade failed: %s", result.stderr)
        return False
    except Exception as e:
        logger.error("pip upgrade error: %s", e)
        return False

def _update_git() -> bool:
    """通过 git pull 更新."""
    from pathlib import Path
    repo_dir = str(Path(__file__).parent.parent.parent)

    try:
        result = subprocess.run(
            ["git", "fetch", "--tags", "origin"],
            capture_output=True, text=True, timeout=60,
            cwd=repo_dir,
        )
        if result.returncode != 0:
            logger.warning("git fetch failed: %s", result.stderr)
            return False

        result = subprocess.run(
            ["git", "reset", "--hard", "origin/main"],
            capture_output=True, text=True, timeout=30,
            cwd=repo_dir,
        )
        if result.returncode != 0:
            logger.warning("git reset failed: %s", result.stderr)
            return False

        result = subprocess.run(
            ["pip", "install", "-e", "."],
            capture_output=True, text=True, timeout=120,
            cwd=repo_dir,
        )
        if result.returncode == 0:
            logger.info("git update succeeded")
            return True
        logger.warning("pip install failed: %s", result.stderr)
        return False
    except Exception as e:
        logger.error("git update error: %s", e)
        return False

async def check_and_notify() -> Optional[str]:
    """静默检查更新，返回提示消息 (无更新返回 None)."""
    try:
        current = get_current_version()
        latest = await check_latest_version()

        if latest and compare_versions(current, latest):
            return f"发现新版本 {latest} (当前 {current})，运行 xjd-agent update 更新"
    except Exception:
        logger.debug("Update check failed")
    return None
