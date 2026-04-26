"""CLI 子命令 — service 系统服务管理.

子命令:
  xjd-agent service install    安装为系统服务（开机自启）
  xjd-agent service uninstall  卸载系统服务
  xjd-agent service status     查看服务状态
  xjd-agent service logs       查看服务日志
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import textwrap
from pathlib import Path

import click
from rich.console import Console

console = Console()

SERVICE_NAME = "xjd-agent"
PLIST_LABEL = "com.xjd.agent"


def _get_xjd_home() -> Path:
    return Path(os.environ.get("XJD_AGENT_HOME", Path.home() / ".xjd-agent"))


def _get_install_dir() -> Path:
    import importlib.util
    spec = importlib.util.find_spec("cli.main")
    if spec and spec.origin:
        return Path(spec.origin).resolve().parent.parent
    return Path.cwd()


def _get_python() -> str:
    return sys.executable


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


# ── Linux (systemd --user) ──────────────────────────────────────

def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _systemd_install(mode: str, port: int) -> None:
    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit = textwrap.dedent(f"""\
        [Unit]
        Description=XJD Agent — AI Agent Platform
        After=network-online.target
        Wants=network-online.target

        [Service]
        Type=simple
        ExecStart={_get_python()} -m cli.main {mode} --host 0.0.0.0 --port {port}
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


def _launchd_install(mode: str, port: int) -> None:
    plist_file = _plist_path()
    plist_file.parent.mkdir(parents=True, exist_ok=True)
    log_path = _get_log_path()
    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
            <key>Label</key>
            <string>{PLIST_LABEL}</string>
            <key>ProgramArguments</key>
            <array>
                <string>{_get_python()}</string>
                <string>-m</string>
                <string>cli.main</string>
                <string>{mode}</string>
                <string>--host</string>
                <string>0.0.0.0</string>
                <string>--port</string>
                <string>{port}</string>
            </array>
            <key>WorkingDirectory</key>
            <string>{_get_install_dir()}</string>
            <key>RunAtLoad</key>
            <true/>
            <key>KeepAlive</key>
            <true/>
            <key>StandardOutPath</key>
            <string>{log_path}</string>
            <key>StandardErrorPath</key>
            <string>{log_path}</string>
            <key>EnvironmentVariables</key>
            <dict>
                <key>XJD_AGENT_HOME</key>
                <string>{_get_xjd_home()}</string>
            </dict>
        </dict>
        </plist>
    """)
    plist_file.write_text(plist)
    subprocess.run(["launchctl", "unload", str(plist_file)], capture_output=True)
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


def _win_install(mode: str, port: int) -> None:
    bat_path = _win_bat_path()
    bat_path.parent.mkdir(parents=True, exist_ok=True)
    log_path = _get_log_path()
    bat = textwrap.dedent(f"""\
        @echo off
        set XJD_AGENT_HOME={_get_xjd_home()}
        cd /d "{_get_install_dir()}"
        :loop
        "{_get_python()}" -m cli.main {mode} --host 0.0.0.0 --port {port} >> "{log_path}" 2>&1
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
        "/rl", "highest", "/f",
    ], check=True)
    subprocess.Popen(f'start /min "" "{bat_path}"', shell=True)
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


# ── Click 命令 ──────────────────────────────────────────────────

@click.group()
def service():
    """系统服务管理（开机自启 / 后台运行）."""
    pass


@service.command()
@click.option("--mode", "-m", default="web", type=click.Choice(["web", "gateway"]),
              help="运行模式 (默认 web)")
@click.option("--port", "-p", default=8080, type=click.IntRange(1, 65535), help="监听端口 (默认 8080)")
def install(mode: str, port: int) -> None:
    """安装为系统服务（开机自启 + 崩溃重启）."""
    plat = _detect_platform()
    console.print(f"  平台: {plat}  模式: {mode}  端口: {port}")
    console.print(f"  Python: {_get_python()}")
    console.print(f"  项目目录: {_get_install_dir()}")
    console.print()
    if plat == "linux":
        _systemd_install(mode, port)
    elif plat == "macos":
        _launchd_install(mode, port)
    elif plat == "windows":
        _win_install(mode, port)
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
