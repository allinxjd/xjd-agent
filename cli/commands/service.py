"""CLI 子命令 — service 系统服务管理.

子命令:
  xjd-agent service install    安装为系统服务（开机自启）
  xjd-agent service uninstall  卸载系统服务
  xjd-agent service restart    重启系统服务
  xjd-agent service status     查看服务状态
  xjd-agent service logs       查看服务日志
"""

from __future__ import annotations

import logging
import os
import platform
import socket
import subprocess
import sys
import textwrap
import time
from html import escape as _xml_escape
from pathlib import Path

import click
from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)

SERVICE_NAME = "xjd-agent"
PLIST_LABEL = "com.xjd.agent"


def _get_xjd_home() -> Path:
    return Path(os.environ.get("XJD_AGENT_HOME", Path.home() / ".xjd-agent"))


def _get_install_dir() -> Path:
    import importlib.util
    spec = importlib.util.find_spec("cli.main")
    if spec and spec.origin:
        return Path(spec.origin).resolve().parent.parent
    raise RuntimeError("无法定位项目目录，请在项目根目录下运行")


def _get_python() -> str:
    """获取当前 Python 解释器路径，优先使用 virtualenv."""
    exe = sys.executable
    venv = os.environ.get("VIRTUAL_ENV")
    if not venv:
        exe_path = Path(exe).resolve()
        for parent in exe_path.parents:
            if (parent / "pyvenv.cfg").exists():
                venv = str(parent)
                break
    if venv:
        for candidate in [
            Path(venv) / "bin" / "python3",
            Path(venv) / "bin" / "python",
            Path(venv) / "Scripts" / "python.exe",
        ]:
            if candidate.exists():
                return str(candidate)
    return exe


def _get_log_path() -> Path:
    log_dir = _get_xjd_home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "service.log"


def _detect_platform() -> str:
    s = platform.system()
    if s == "Linux":
        return "linux"
    if s == "Darwin":
        return "macos"
    if s == "Windows":
        return "windows"
    return "unknown"


def _check_port_available(port: int) -> bool:
    """检测端口是否可用."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.settimeout(1)
        sock.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _kill_port_occupant(port: int) -> bool:
    """杀掉占用指定端口的 xjd-agent 进程，返回是否成功释放."""
    if _check_port_available(port):
        return True
    try:
        r = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p.strip() for p in r.stdout.strip().splitlines() if p.strip()]
        if not pids:
            return False
        for pid in pids:
            try:
                cmd_r = subprocess.run(
                    ["ps", "-p", pid, "-o", "command="],
                    capture_output=True, text=True, timeout=5,
                )
                cmd_line = cmd_r.stdout.strip()
                if "xjd" in cmd_line or "cli.main" in cmd_line:
                    os.kill(int(pid), 15)  # SIGTERM
                    logger.info("Killed old xjd-agent process %s on port %d", pid, port)
            except (ProcessLookupError, ValueError):
                pass
        time.sleep(1)
        return _check_port_available(port)
    except Exception as e:
        logger.debug("_kill_port_occupant error: %s", e)
        return False


# ── Linux (systemd --user) ──────────────────────────────────────

def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _systemd_install(port: int) -> None:
    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=XJD Agent — AI Agent Platform
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={_get_python()} -m cli.main web --host 0.0.0.0 --port {port}
        WorkingDirectory={_get_install_dir()}
        Restart=on-failure
        RestartSec=5
        Environment=XJD_AGENT_HOME={_get_xjd_home()}

        [Install]
        WantedBy=default.target
    """)
    unit_path.write_text(unit)
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)
    try:
        user = os.getlogin()
    except OSError:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    subprocess.run(["loginctl", "enable-linger", user], capture_output=True)
    console.print(f"  systemd 用户服务已安装: {unit_path}")
    console.print(f"  服务已启动，访问 http://localhost:{port}")


def _systemd_uninstall() -> None:
    subprocess.run(["systemctl", "--user", "stop", SERVICE_NAME], capture_output=True)
    subprocess.run(["systemctl", "--user", "disable", SERVICE_NAME], capture_output=True)
    unit_path = _systemd_unit_path()
    if unit_path.exists():
        unit_path.unlink()
    subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    console.print("  systemd 服务已卸载")


def _systemd_status() -> None:
    r = subprocess.run(["systemctl", "--user", "status", SERVICE_NAME],
                       capture_output=True, text=True)
    console.print(r.stdout or r.stderr or "服务未安装")


def _systemd_logs() -> None:
    try:
        subprocess.run(["journalctl", "--user", "-u", SERVICE_NAME, "-f", "--no-pager", "-n", "50"])
    except KeyboardInterrupt:
        pass


# ── macOS (launchd) ─────────────────────────────────────────────

def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _launchd_install(port: int) -> None:
    plist_file = _plist_path()
    plist_file.parent.mkdir(parents=True, exist_ok=True)
    log_path = _get_log_path()
    _e = _xml_escape
    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{_e(PLIST_LABEL)}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{_e(str(_get_python()))}</string>
                <string>-m</string>
                <string>cli.main</string>
                <string>web</string>
                <string>--host</string>
                <string>0.0.0.0</string>
                <string>--port</string>
                <string>{port}</string>
            </array>
            <key>WorkingDirectory</key>
            <string>{_e(str(_get_install_dir()))}</string>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <dict>
                <key>SuccessfulExit</key>
                <false/>
            </dict>
            <key>StandardOutPath</key>
            <string>{_e(str(log_path))}</string>
            <key>StandardErrorPath</key>
            <string>{_e(str(log_path))}</string>
            <key>EnvironmentVariables</key>
            <dict>
                <key>XJD_AGENT_HOME</key>
                <string>{_e(str(_get_xjd_home()))}</string>
            </dict>
        </dict>
        </plist>
    """)
    plist_file.write_text(plist)
    subprocess.run(["launchctl", "unload", str(plist_file)], capture_output=True)
    _kill_port_occupant(port)
    subprocess.run(["launchctl", "load", "-w", str(plist_file)], check=True)
    console.print(f"  launchd 服务已安装: {plist_file}")
    console.print(f"  服务已启动，访问 http://localhost:{port}")
    console.print(f"  日志: {log_path}")


def _launchd_uninstall() -> None:
    plist_file = _plist_path()
    if plist_file.exists():
        subprocess.run(["launchctl", "unload", "-w", str(plist_file)], capture_output=True)
        plist_file.unlink()
    console.print("  launchd 服务已卸载")


def _launchd_status() -> None:
    r = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
    lines = [ln for ln in (r.stdout or "").splitlines() if PLIST_LABEL in ln]
    if lines:
        console.print(f"  {lines[0]}")
        plist_file = _plist_path()
        if plist_file.exists():
            console.print(f"  配置: {plist_file}")
    else:
        console.print("  服务未安装")


def _launchd_logs() -> None:
    log_path = _get_log_path()
    if not log_path.exists():
        console.print(f"  日志文件不存在: {log_path}")
        return
    try:
        subprocess.run(["tail", "-f", "-n", "50", str(log_path)])
    except KeyboardInterrupt:
        pass


# ── Windows (Task Scheduler) ────────────────────────────────────

def _win_bat_path() -> Path:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "xjd-agent" / "start-service.bat"


def _validate_path_for_batch(path: str) -> None:
    """Reject paths with cmd metacharacters that could break batch scripts."""
    dangerous = set('&|<>^%!"')
    found = dangerous & set(path)
    if found:
        raise RuntimeError(
            f"路径包含不安全字符 {found}: {path}\n"
            "请将项目安装到不含特殊字符的路径下"
        )


def _win_install(port: int) -> None:
    bat_path = _win_bat_path()
    bat_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = _get_log_path()
    python_path = _get_python()
    install_dir = str(_get_install_dir())
    xjd_home = str(_get_xjd_home())
    for p in [python_path, install_dir, xjd_home, str(log_path)]:
        _validate_path_for_batch(p)
    bat = textwrap.dedent(f"""\
        @echo off
        set "XJD_AGENT_HOME={xjd_home}"
        cd /d "{install_dir}"
        :loop
        "{python_path}" -m cli.main web --host 0.0.0.0 --port {port} >> "{log_path}" 2>&1
        echo [%date% %time%] Service exited, restarting in 5s... >> "{log_path}"
        timeout /t 5 /nobreak >nul
        goto loop
    """)
    bat_path.write_text(bat)
    subprocess.run(["schtasks", "/delete", "/tn", SERVICE_NAME, "/f"], capture_output=True)
    subprocess.run([
        "schtasks", "/create", "/tn", SERVICE_NAME,
        "/sc", "onlogon",
        "/tr", f'cmd /c start /min "" "{bat_path}"',
        "/rl", "limited", "/f",
    ], check=True)
    subprocess.Popen(
        ["cmd", "/c", "start", "/min", "", str(bat_path)],
        close_fds=True,
    )
    console.print(f"  Windows 计划任务已创建: {SERVICE_NAME}")
    console.print(f"  启动脚本: {bat_path}")
    console.print(f"  服务已启动，访问 http://localhost:{port}")
    console.print(f"  日志: {log_path}")


def _win_uninstall() -> None:
    subprocess.run(["schtasks", "/delete", "/tn", SERVICE_NAME, "/f"], capture_output=True)
    subprocess.run(["taskkill", "/f", "/fi", f"WINDOWTITLE eq {SERVICE_NAME}*"], capture_output=True)
    bat_path = _win_bat_path()
    if bat_path.exists():
        bat_path.unlink()
    console.print("  Windows 计划任务已删除")


def _win_status() -> None:
    r = subprocess.run(["schtasks", "/query", "/tn", SERVICE_NAME],
                       capture_output=True, text=True)
    if r.returncode == 0:
        console.print(r.stdout.strip())
    else:
        console.print("  服务未安装")


def _win_logs() -> None:
    log_path = _get_log_path()
    if not log_path.exists():
        console.print(f"  日志文件不存在: {log_path}")
        return
    try:
        subprocess.run(["powershell", "Get-Content", "-Wait", "-Tail", "50", str(log_path)])
    except KeyboardInterrupt:
        pass


# ── 辅助函数（供其他模块调用）─────────────────────────────────

def is_service_installed() -> bool:
    """检测系统服务是否已注册."""
    plat = _detect_platform()
    if plat == "linux":
        return _systemd_unit_path().exists()
    elif plat == "macos":
        return _plist_path().exists()
    elif plat == "windows":
        r = subprocess.run(["schtasks", "/query", "/tn", SERVICE_NAME],
                           capture_output=True)
        return r.returncode == 0
    return False


def restart_service() -> bool:
    """重启已注册的系统服务. 返回是否成功."""
    plat = _detect_platform()
    if plat == "linux":
        r = subprocess.run(["systemctl", "--user", "restart", SERVICE_NAME],
                           capture_output=True)
        return r.returncode == 0
    elif plat == "macos":
        plist = _plist_path()
        if not plist.exists():
            return False
        uid = os.getuid()
        r = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{PLIST_LABEL}"],
            capture_output=True,
        )
        return r.returncode == 0
    elif plat == "windows":
        subprocess.run(["schtasks", "/end", "/tn", SERVICE_NAME], capture_output=True)
        r = subprocess.run(["schtasks", "/run", "/tn", SERVICE_NAME], capture_output=True)
        return r.returncode == 0
    return False


def install_service_silent(port: int = 8080) -> bool:
    """静默安装系统服务. 返回是否成功."""
    plat = _detect_platform()
    try:
        if plat == "linux":
            _systemd_install(port)
        elif plat == "macos":
            _launchd_install(port)
        elif plat == "windows":
            _win_install(port)
        else:
            return False
        return True
    except Exception as e:
        logger.error("服务安装失败: %s", e)
        console.print(f"  [red]错误详情: {e}[/red]")
        return False


# ── Click 命令 ──────────────────────────────────────────────────

@click.group()
def service():
    """系统服务管理（开机自启 / 后台运行）."""
    pass


@service.command()
@click.option("--port", "-p", default=8080, type=click.IntRange(1, 65535), help="监听端口 (默认 8080)")
def install(port: int) -> None:
    """安装为系统服务（开机自启 + 崩溃重启）."""
    plat = _detect_platform()
    console.print(f"  平台: {plat}  端口: {port}")
    console.print(f"  Python: {_get_python()}")
    console.print(f"  项目目录: {_get_install_dir()}")
    console.print()
    if not _check_port_available(port):
        console.print(f"  [yellow]端口 {port} 已被占用，服务启动后可能冲突[/yellow]")
    if plat == "linux":
        _systemd_install(port)
    elif plat == "macos":
        _launchd_install(port)
    elif plat == "windows":
        _win_install(port)
    else:
        console.print(f"  [red]不支持的平台: {plat}[/red]")
        console.print("  请使用 Docker 部署: docker compose up -d")


@service.command()
def uninstall() -> None:
    """卸载系统服务."""
    plat = _detect_platform()
    if plat == "linux":
        _systemd_uninstall()
    elif plat == "macos":
        _launchd_uninstall()
    elif plat == "windows":
        _win_uninstall()
    else:
        console.print(f"  [red]不支持的平台: {plat}[/red]")


@service.command(name="restart")
def restart_cmd() -> None:
    """重启系统服务."""
    if not is_service_installed():
        console.print("  [yellow]服务未安装，请先运行: xjd-agent gateway[/yellow]")
        return
    if restart_service():
        console.print("  [green]服务已重启[/green]")
    else:
        console.print("  [red]服务重启失败[/red]")


@service.command()
def status() -> None:
    """查看服务状态."""
    plat = _detect_platform()
    if plat == "linux":
        _systemd_status()
    elif plat == "macos":
        _launchd_status()
    elif plat == "windows":
        _win_status()
    else:
        console.print(f"  [red]不支持的平台: {plat}[/red]")


@service.command()
def logs() -> None:
    """查看服务日志 (实时跟踪)."""
    plat = _detect_platform()
    if plat == "linux":
        _systemd_logs()
    elif plat == "macos":
        _launchd_logs()
    elif plat == "windows":
        _win_logs()
    else:
        console.print(f"  [red]不支持的平台: {plat}[/red]")
