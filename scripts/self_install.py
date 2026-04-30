#!/usr/bin/env python3
"""Self-contained pip installer for xjd-agent.

Called by the updater AFTER new source code has been fetched (git or tarball).
This script lives in the NEW code, so any future fixes here are automatically
picked up — breaking the chicken-and-egg cycle where old updater code can't
benefit from new install logic.

Exit codes:
    0 — install succeeded
    1 — all install methods failed

Usage:
    python3 scripts/self_install.py [--cwd /path/to/repo]
"""

import os
import platform
import site
import subprocess
import sys
from pathlib import Path


def _in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def _pip_base_args() -> list[str]:
    return [sys.executable, "-m", "pip"]


def _mirror_args() -> list[str]:
    return ["-i", "https://mirrors.aliyun.com/pypi/simple/",
            "--trusted-host", "mirrors.aliyun.com"]


def _break_system_args() -> list[str]:
    if not _in_venv():
        return ["--break-system-packages"]
    return []


def _run(args: list[str], cwd: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _check_user_bin_in_path() -> None:
    if _in_venv():
        return
    if platform.system() == "Darwin":
        ver = platform.python_version_tuple()
        user_bin = os.path.expanduser(f"~/Library/Python/{ver[0]}.{ver[1]}/bin")
    else:
        user_bin = site.getusersitepackages().replace(
            "/lib/python", "/bin").rsplit("/lib/", 1)[0] + "/bin"
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    if user_bin not in path_dirs:
        print(f"[self_install] WARNING: {user_bin} is not in PATH")
        print(f"[self_install] Run: export PATH=\"{user_bin}:$PATH\"")


def install(cwd: str) -> bool:
    """Try pip install with escalating fallbacks. Returns True on success."""

    # Strategy 1: force-reinstall without deps (fast ~3s — only reinstalls xjd-agent,
    # deps already present from previous install)
    args = [*_pip_base_args(), "install", "--force-reinstall", "--no-deps",
            ".", *_mirror_args(), *_break_system_args()]
    print("[self_install] Strategy 1: force-reinstall --no-deps")
    r = _run(args, cwd, timeout=120)
    if r.returncode == 0:
        print("[self_install] OK")
        return True
    print(f"[self_install] Failed: {r.stderr[:200]}")

    # Strategy 2: force-reinstall (slower — re-downloads all deps, but ensures
    # package-data like builtin_skills is refreshed)
    args = [*_pip_base_args(), "install", "--force-reinstall",
            ".", *_mirror_args(), *_break_system_args()]
    print("[self_install] Strategy 2: force-reinstall")
    r = _run(args, cwd, timeout=300)
    if r.returncode == 0:
        print("[self_install] OK")
        return True
    print(f"[self_install] Failed: {r.stderr[:200]}")

    # Strategy 3: --user install (when system site-packages is read-only)
    if not _in_venv():
        args = [*_pip_base_args(), "install", "--user",
                ".", *_mirror_args(), "--break-system-packages"]
        print("[self_install] Strategy 3: --user install")
        r = _run(args, cwd, timeout=120)
        if r.returncode == 0:
            _check_user_bin_in_path()
            print("[self_install] OK (--user)")
            return True
        print(f"[self_install] Failed: {r.stderr[:200]}")

    # Strategy 4: no mirror (mirror might be blocked or slow)
    args = [*_pip_base_args(), "install", ".", *_break_system_args()]
    print("[self_install] Strategy 4: no mirror")
    r = _run(args, cwd, timeout=120)
    if r.returncode == 0:
        print("[self_install] OK")
        return True
    print(f"[self_install] Failed: {r.stderr[:200]}")

    print("[self_install] All strategies failed")
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=None, help="repo directory")
    args = parser.parse_args()

    cwd = args.cwd or str(Path(__file__).parent.parent)
    if not Path(cwd, "pyproject.toml").exists():
        print(f"[self_install] ERROR: no pyproject.toml in {cwd}")
        sys.exit(1)

    print(f"[self_install] Python: {sys.executable}")
    print(f"[self_install] venv: {_in_venv()}")
    print(f"[self_install] cwd: {cwd}")

    ok = install(cwd)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
