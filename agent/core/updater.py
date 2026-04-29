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
import platform
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)

# 项目信息
PACKAGE_NAME = "xjd-agent"
GITHUB_REPO = "allinxjd/xjd-agent"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
GITHUB_TAGS_URL = f"https://api.github.com/repos/{GITHUB_REPO}/tags"


def _detect_system_proxy() -> Optional[str]:
    """检测系统 HTTP 代理（macOS networksetup / 环境变量）."""
    import os
    for var in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        val = os.environ.get(var)
        if val:
            return val
    if platform.system() == "Darwin":
        try:
            r = subprocess.run(
                ["networksetup", "-getsecurewebproxy", "Wi-Fi"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                lines = {l.split(":")[0].strip(): l.split(":", 1)[1].strip()
                         for l in r.stdout.splitlines() if ":" in l}
                if lines.get("Enabled") == "Yes":
                    server = lines.get("Server", "")
                    port = lines.get("Port", "")
                    if server and port:
                        return f"http://{server}:{port}"
        except Exception:
            pass
    return None


def _git_proxy_args() -> list[str]:
    """返回 git -c http.proxy=... 参数（如果检测到代理）."""
    proxy = _detect_system_proxy()
    if proxy:
        return ["-c", f"http.proxy={proxy}", "-c", f"https.proxy={proxy}"]
    return []

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
    return "0.0.0"

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
    proxy_args = _git_proxy_args()
    for attempt in range(2):
        try:
            r = subprocess.run(
                ["git", "-c", "http.version=HTTP/1.1", *proxy_args, "fetch", "origin", "main"],
                capture_output=True, text=True, timeout=45,
                cwd=str(repo_dir),
            )
            if r.returncode == 0:
                return True
            logger.debug("git fetch attempt %d failed (rc=%d): %s",
                         attempt + 1, r.returncode, r.stderr.strip())
        except subprocess.TimeoutExpired:
            logger.debug("git fetch attempt %d timed out", attempt + 1)
        except Exception as e:
            logger.debug("git fetch attempt %d error: %s", attempt + 1, e)
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

    优先 git fetch 比较 commit 差异，失败则通过 GitHub API (urllib) 检查。
    """
    repo_dir = _git_repo_dir()
    if repo_dir:
        if _git_fetch(repo_dir):
            pending = _git_pending_commits(repo_dir)
            if pending:
                return f"commit:{len(pending)}"
            return get_current_version() or "0.0.0"
        # git fetch 失败 → 通过 GitHub API 检查（urllib 自动走系统代理）
        try:
            from urllib.request import urlopen, Request
            import json
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/commits/main"
            req = Request(api_url, headers={"User-Agent": "xjd-agent-updater"})
            resp = urlopen(req, timeout=15)
            remote_sha = json.loads(resp.read()).get("sha", "")[:7]
            local_sha = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=str(repo_dir),
            ).stdout.strip()
            if remote_sha and local_sha and remote_sha != local_sha:
                return "commit:?"
        except Exception as e:
            logger.debug("GitHub API check failed: %s", e)
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
    import sys as _sys
    try:
        pip_args = [_sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME]
        if _sys.prefix == _sys.base_prefix:
            pip_args.append("--break-system-packages")
        result = subprocess.run(
            pip_args, capture_output=True, text=True, timeout=120,
        )
        if result.returncode == 0:
            logger.info("pip upgrade succeeded")
            return True
        logger.warning("pip upgrade failed: %s", result.stderr[:200])
        return False
    except Exception as e:
        logger.error("pip upgrade error: %s", e)
        return False

def _update_git() -> bool:
    """通过 git pull 更新，失败则回退到 tarball 下载."""
    repo_dir = _git_repo_dir()
    if not repo_dir:
        logger.warning("Not a git repository")
        return False
    repo = str(repo_dir)

    pulled = False
    try:
        proxy_args = _git_proxy_args()
        result = subprocess.run(
            ["git", "-c", "http.version=HTTP/1.1", *proxy_args, "pull", "--ff-only", "origin", "main"],
            capture_output=True, text=True, timeout=60,
            cwd=repo,
        )
        if result.returncode == 0:
            pulled = True
        else:
            logger.warning("git pull failed: %s", result.stderr)
    except subprocess.TimeoutExpired:
        logger.warning("git pull timed out")
    except Exception as e:
        logger.warning("git pull error: %s", e)

    if not pulled:
        logger.info("git pull failed, falling back to tarball download...")
        pulled = _update_tarball(repo_dir)

    if not pulled:
        return False

    _run_pip_install(repo)
    return True


def _run_pip_install(cwd: str) -> bool:
    """运行 pip install -e .，自动处理 externally-managed-environment."""
    import sys as _sys
    pip_args = [_sys.executable, "-m", "pip", "install", "-e", ".",
                "-i", "https://mirrors.aliyun.com/pypi/simple/",
                "--trusted-host", "mirrors.aliyun.com"]
    if _sys.prefix == _sys.base_prefix:
        pip_args.append("--break-system-packages")
    result = subprocess.run(
        pip_args, capture_output=True, text=True, timeout=120, cwd=cwd,
    )
    if result.returncode != 0:
        logger.warning("pip install failed, trying --user: %s", result.stderr[:200])
        pip_args_user = [_sys.executable, "-m", "pip", "install", "--user", "-e", ".",
                         "-i", "https://mirrors.aliyun.com/pypi/simple/",
                         "--trusted-host", "mirrors.aliyun.com",
                         "--break-system-packages"]
        result = subprocess.run(
            pip_args_user, capture_output=True, text=True, timeout=120, cwd=cwd,
        )
    return result.returncode == 0


def _update_tarball(repo_dir: "Path") -> bool:
    """通过 GitHub tarball 更新（Python urllib 自动走 macOS 系统代理）."""
    import io
    import shutil
    import tarfile
    import tempfile
    from pathlib import Path
    from urllib.request import urlopen, Request

    tarball_url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.tar.gz"
    try:
        logger.info("Downloading %s ...", tarball_url)
        req = Request(tarball_url, headers={"User-Agent": "xjd-agent-updater"})
        resp = urlopen(req, timeout=120)
        data = resp.read()
        logger.info("Downloaded %.1f KB", len(data) / 1024)
    except Exception as e:
        logger.error("Tarball download failed: %s", e)
        return False

    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="xjd-update-"))
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            tf.extractall(tmp_dir)
        extracted = list(tmp_dir.iterdir())
        if len(extracted) != 1 or not extracted[0].is_dir():
            logger.error("Unexpected tarball structure")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return False
        src = extracted[0]
        preserve = {".git", ".env", ".env.local", "node_modules",
                    "__pycache__", ".xjd-agent"}
        for item in src.iterdir():
            if item.name in preserve:
                continue
            dest = repo_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.info("Tarball update applied successfully")
        return True
    except Exception as e:
        logger.error("Tarball extract failed: %s", e)
        return False

async def check_and_notify() -> Optional[str]:
    """静默检查更新，返回提示消息 (无更新返回 None)."""
    try:
        current = get_current_version()
        latest = await check_latest_version()

        if latest and latest.startswith("commit:"):
            parts = latest.split(":", 1)
            count = parts[1] if len(parts) > 1 else "?"
            return f"发现 {count} 个新提交可更新，运行 xjd-agent update --auto 更新"
        if latest and compare_versions(current, latest):
            return f"发现新版本 {latest} (当前 {current})，运行 xjd-agent update 更新"
    except Exception:
        logger.debug("Update check failed")
    return None
