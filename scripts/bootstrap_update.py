#!/usr/bin/env python3
"""xjd-agent 引导更新脚本.

解决「鸡生蛋」问题：旧版本的 updater 无法连接 GitHub，
此脚本通过 Python urllib（自动走 macOS 系统代理）下载最新代码。

用法（一行命令）:
    python3 -c "from urllib.request import urlopen; exec(urlopen('https://raw.githubusercontent.com/allinxjd/xjd-agent/main/scripts/bootstrap_update.py').read())"

或本地运行:
    python3 scripts/bootstrap_update.py
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

REPO = "allinxjd/xjd-agent"
TARBALL_URL = f"https://github.com/{REPO}/archive/refs/heads/main.tar.gz"
PRESERVE = {".git", ".env", ".env.local", "node_modules", "__pycache__",
            ".xjd-agent", ".venv", "venv"}


def find_install_dir() -> Path:
    """查找 xjd-agent 安装目录."""
    # 1. 当前目录
    if (Path.cwd() / "pyproject.toml").exists():
        txt = (Path.cwd() / "pyproject.toml").read_text()
        if "xjd-agent" in txt:
            return Path.cwd()
    # 2. 通过 importlib 找
    try:
        import agent
        p = Path(agent.__file__).parent.parent
        if (p / "pyproject.toml").exists():
            return p
    except Exception:
        pass
    # 3. 常见位置
    for d in [Path.home() / "xjd-agent", Path.home() / ".xjd-agent",
              Path("/opt/xjd-agent")]:
        if (d / "pyproject.toml").exists():
            return d
    return Path.cwd()


def main():
    install_dir = find_install_dir()
    print(f"[bootstrap] 安装目录: {install_dir}")

    print(f"[bootstrap] 下载最新代码: {TARBALL_URL}")
    req = Request(TARBALL_URL, headers={"User-Agent": "xjd-agent-bootstrap"})
    resp = urlopen(req, timeout=120)
    data = resp.read()
    print(f"[bootstrap] 下载完成: {len(data) / 1024:.1f} KB")

    tmp_dir = Path(tempfile.mkdtemp(prefix="xjd-bootstrap-"))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        tf.extractall(tmp_dir)

    extracted = list(tmp_dir.iterdir())
    if len(extracted) != 1 or not extracted[0].is_dir():
        print("[bootstrap] 错误: tarball 结构异常")
        sys.exit(1)
    src = extracted[0]

    updated = 0
    for item in src.iterdir():
        if item.name in PRESERVE:
            continue
        dest = install_dir / item.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
        updated += 1

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"[bootstrap] 已更新 {updated} 个文件/目录")

    print("[bootstrap] 安装依赖...")
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-e", ".",
         "-i", "https://mirrors.aliyun.com/pypi/simple/",
         "--trusted-host", "mirrors.aliyun.com"],
        cwd=str(install_dir),
    )
    print("[bootstrap] 更新完成!")


if __name__ == "__main__":
    main()
