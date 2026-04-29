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


def _detect_proxy() -> str | None:
    """检测代理: 环境变量 > config.yaml > scutil > networksetup 多接口."""
    import platform as _plat
    import re as _re
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "ALL_PROXY", "all_proxy"):
        val = os.environ.get(key)
        if val:
            return val
    try:
        import yaml
        cfg_path = Path(os.environ.get("XJD_AGENT_HOME",
                                       Path.home() / ".xjd-agent")) / "config.yaml"
        if cfg_path.exists():
            with open(cfg_path) as f:
                data = yaml.safe_load(f) or {}
            if data.get("proxy"):
                return data["proxy"]
    except Exception:
        pass
    if _plat.system() == "Darwin":
        try:
            r = subprocess.run(["scutil", "--proxy"],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                out = r.stdout
                if "HTTPSEnable : 1" in out:
                    host = _re.search(r"HTTPSProxy\s*:\s*(\S+)", out)
                    port = _re.search(r"HTTPSPort\s*:\s*(\d+)", out)
                    if host and port:
                        return f"http://{host.group(1)}:{port.group(1)}"
                if "HTTPEnable : 1" in out:
                    host = _re.search(r"HTTPProxy\s*:\s*(\S+)", out)
                    port = _re.search(r"HTTPPort\s*:\s*(\d+)", out)
                    if host and port:
                        return f"http://{host.group(1)}:{port.group(1)}"
        except Exception:
            pass
        for iface in ("Ethernet", "Wi-Fi", "USB 10/100/1000 LAN", "iPhone USB"):
            try:
                r = subprocess.run(
                    ["networksetup", "-getsecurewebproxy", iface],
                    capture_output=True, text=True, timeout=3)
                if r.returncode == 0:
                    lines = {l.split(":")[0].strip(): l.split(":", 1)[1].strip()
                             for l in r.stdout.splitlines() if ":" in l}
                    if lines.get("Enabled") == "Yes":
                        server = lines.get("Server", "")
                        port = lines.get("Port", "")
                        if server and port:
                            return f"http://{server}:{port}"
            except Exception:
                continue
    return None


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
    # 3. pip direct_url.json
    try:
        import json
        from importlib.metadata import distribution
        dist = distribution("xjd-agent")
        du = dist.read_text("direct_url.json")
        if du:
            url = json.loads(du).get("url", "")
            if url.startswith("file://"):
                d = Path(url[7:])
                if (d / "pyproject.toml").exists():
                    return d
    except Exception:
        pass
    # 4. 缓存路径
    try:
        cache = Path(os.environ.get("XJD_AGENT_HOME",
                                    Path.home() / ".xjd-agent")) / ".repo_path"
        if cache.exists():
            d = Path(cache.read_text().strip())
            if (d / "pyproject.toml").exists():
                return d
    except Exception:
        pass
    # 5. 常见位置
    home = Path.home()
    for d in [home / "xjd-agent", home / ".xjd-agent",
              Path("/opt/xjd-agent"),
              home / "code" / "xjd-agent", home / "Code" / "xjd-agent",
              home / "projects" / "xjd-agent", home / "dev" / "xjd-agent"]:
        if (d / "pyproject.toml").exists():
            return d
    return Path.cwd()


def main():
    install_dir = find_install_dir()
    print(f"[bootstrap] 安装目录: {install_dir}")

    proxy = _detect_proxy()
    if proxy:
        print(f"[bootstrap] 检测到代理: {proxy}")

    print(f"[bootstrap] 下载最新代码: {TARBALL_URL}")
    req = Request(TARBALL_URL, headers={"User-Agent": "xjd-agent-bootstrap"})
    if proxy:
        from urllib.request import build_opener, ProxyHandler
        opener = build_opener(ProxyHandler({"http": proxy, "https": proxy}))
        resp = opener.open(req, timeout=120)
    else:
        resp = urlopen(req, timeout=120)
    data = resp.read()
    print(f"[bootstrap] 下载完成: {len(data) / 1024:.1f} KB")

    tmp_dir = Path(tempfile.mkdtemp(prefix="xjd-bootstrap-"))
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        tf.extractall(tmp_dir, filter="data" if hasattr(tarfile, "data_filter") else None)

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
    pip_args = [sys.executable, "-m", "pip", "install", ".",
                "-i", "https://mirrors.aliyun.com/pypi/simple/",
                "--trusted-host", "mirrors.aliyun.com"]
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        pip_args.append("--break-system-packages")
    r = subprocess.run(pip_args, cwd=str(install_dir), capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[bootstrap] pip install 失败，尝试 --user 模式...")
        pip_args_user = [sys.executable, "-m", "pip", "install", "--user", ".",
                         "-i", "https://mirrors.aliyun.com/pypi/simple/",
                         "--trusted-host", "mirrors.aliyun.com"]
        if not in_venv:
            pip_args_user.append("--break-system-packages")
        r = subprocess.run(pip_args_user, cwd=str(install_dir), capture_output=True, text=True)
        if r.returncode == 0:
            _print_path_hint()
        else:
            print(f"[bootstrap] pip install --user 也失败: {r.stderr[:300]}")
            print("[bootstrap] 请手动运行: cd {} && pip install .".format(install_dir))
            return
    print("[bootstrap] 更新完成!")
    print("[bootstrap] 运行 xjd-agent --version 验证")


def _print_path_hint():
    """提示用户将 bin 目录加入 PATH."""
    import platform as _plat
    import site
    if _plat.system() == "Darwin":
        ver = _plat.python_version_tuple()
        user_bin = os.path.expanduser(f"~/Library/Python/{ver[0]}.{ver[1]}/bin")
    else:
        user_bin = site.getusersitepackages().replace("/lib/python", "/bin").rsplit("/lib/", 1)[0] + "/bin"
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    if user_bin not in path_dirs:
        print(f"[bootstrap] xjd-agent 安装到了 {user_bin}")
        print(f"[bootstrap] 请运行: export PATH=\"{user_bin}:$PATH\"")
        shell_rc = "~/.zshrc" if _plat.system() == "Darwin" else "~/.bashrc"
        print(f"[bootstrap] 永久生效: echo 'export PATH=\"{user_bin}:$PATH\"' >> {shell_rc}")


if __name__ == "__main__":
    main()
