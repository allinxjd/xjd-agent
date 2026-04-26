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

def _git_repo_dir() -> Optional["Path"]:
    from pathlib import Path
    repo_dir = Path(__file__).parent.parent.parent
    if (repo_dir / ".git").exists():
        return repo_dir
    return None


def _git_fetch(repo_dir: "Path") -> bool:
    try:
        r = subprocess.run(
            ["git", "fetch", "origin"],
            capture_output=True, text=True, timeout=30,
            cwd=str(repo_dir),
        )
        return r.returncode == 0
    except Exception as e:
        logger.debug("git fetch failed: %s", e)
        return False


def _git_pending_commits(repo_dir: "Path") -> list[str]:
    try:
        r = subprocess.run(
            ["git", "log", "--oneline", "HEAD..origin/main"],
            capture_output=True, text=True, timeout=10,
            cwd=str(repo_dir),
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()
    except Exception:
        pass
    return []


async def check_latest_version() -> Optional[str]:
    """检查最新版本.

    git clone 用户: fetch 后比较 commit 差异 (最可靠)
    pip 用户: 查 PyPI

    Returns:
        版本号字符串，或 "commit:<N>" 表示有 N 个新提交，None 表示无法检查
    """
    # git clone 用户 — 直接比较 commit 差异，不依赖 tag
    repo_dir = _git_repo_dir()
    if repo_dir:
        if _git_fetch(repo_dir):
            pending = _git_pending_commits(repo_dir)
            if pending:
                return f"commit:{len(pending)}"
            return get_current_version() or "0.0.0"
        return None

    # pip 用户 — 查 PyPI
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(PYPI_JSON_URL)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("info", {}).get("version")
    except Exception as e:
        logger.debug("PyPI check failed: %s", e)

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
    repo_dir = _git_repo_dir()
    if not repo_dir:
        logger.warning("Not a git repository")
        return False
    repo = str(repo_dir)

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            capture_output=True, text=True, timeout=60,
            cwd=repo,
        )
        if result.returncode != 0:
            logger.warning("git pull failed: %s", result.stderr)
            return False

        result = subprocess.run(
            ["pip", "install", "-e", "."],
            capture_output=True, text=True, timeout=120,
            cwd=repo,
        )
        if result.returncode == 0:
            logger.info("git update succeeded")
            return True
        if "SSL" in (result.stderr or ""):
            logger.info("pip SSL failed, retrying with mirror")
            result = subprocess.run(
                ["pip", "install", "-e", ".",
                 "-i", "https://mirrors.aliyun.com/pypi/simple/",
                 "--trusted-host", "mirrors.aliyun.com"],
                capture_output=True, text=True, timeout=120,
                cwd=repo,
            )
            if result.returncode == 0:
                logger.info("git update succeeded (mirror)")
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

        if latest and latest.startswith("commit:"):
            count = latest.split(":")[1]
            return f"发现 {count} 个新提交可更新，运行 xjd-agent update --auto 更新"
        if latest and compare_versions(current, latest):
            return f"发现新版本 {latest} (当前 {current})，运行 xjd-agent update 更新"
    except Exception:
        logger.debug("Update check failed")
    return None
