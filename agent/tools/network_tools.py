"""Network tools: DNS lookup and port scanning."""

import asyncio
import logging
import socket

logger = logging.getLogger(__name__)


async def _dns_lookup(domain: str, record_type: str = "A", **kw) -> str:
    """DNS 查询."""
    try:
        if record_type.upper() == "A":
            results = socket.getaddrinfo(domain, None, socket.AF_INET)
            ips = sorted(set(r[4][0] for r in results))
            return f"{domain} A 记录:\n" + "\n".join(ips)
        elif record_type.upper() == "AAAA":
            results = socket.getaddrinfo(domain, None, socket.AF_INET6)
            ips = sorted(set(r[4][0] for r in results))
            return f"{domain} AAAA 记录:\n" + "\n".join(ips)
        else:
            info = socket.gethostbyname_ex(domain)
            return f"主机名: {info[0]}\n别名: {info[1]}\nIP: {info[2]}"
    except socket.gaierror as e:
        return f"DNS 查询失败: {e}"


async def _port_scanner(host: str, ports: str = "80,443", timeout: float = 2.0, **kw) -> str:
    """端口扫描."""
    port_list: list[int] = []
    for part in ports.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            port_list.extend(range(int(start), int(end) + 1))
        else:
            port_list.append(int(part))

    results: list[tuple[int, bool]] = []

    async def check(port: int) -> tuple[int, bool]:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return (port, True)
        except (OSError, asyncio.TimeoutError):
            return (port, False)

    results = await asyncio.gather(*(check(p) for p in port_list[:100]))
    open_ports = sorted(p for p, is_open in results if is_open)
    closed_count = sum(1 for _, is_open in results if not is_open)
    lines = [f"扫描 {host}: {len(port_list)} 端口"]
    if open_ports:
        lines.append(f"开放: {open_ports}")
    lines.append(f"关闭: {closed_count} 个")
    return "\n".join(lines)


def register_network_tools(registry):
    """Register network tools."""

    registry.register(
        name="dns_lookup",
        description="DNS 域名解析查询。",
        parameters={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "域名"},
                "record_type": {"type": "string", "description": "记录类型", "default": "A"},
            },
            "required": ["domain"],
        },
        handler=_dns_lookup,
        category="network",
    )

    registry.register(
        name="port_scanner",
        description="扫描主机端口是否开放。",
        parameters={
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "目标主机"},
                "ports": {"type": "string", "description": "端口 (如 80,443,8000-8100)", "default": "80,443"},
                "timeout": {"type": "number", "description": "超时秒数", "default": 2.0},
            },
            "required": ["host"],
        },
        handler=_port_scanner,
        category="network",
        requires_approval=True,
    )
