"""Agent 注册与发现 — Redis-backed 服务注册表.

每个 Agent 实例启动时注册自己，定期心跳续期。
其他 Agent 或 Coordinator 可按角色/能力发现在线 Agent。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

REGISTRY_KEY_PREFIX = "xjd:agents:"
REGISTRY_TTL = 60  # 秒，心跳间隔应 < TTL/2


@dataclass
class AgentInfo:
    """Agent 注册信息."""

    agent_id: str = ""
    role: str = ""
    capabilities: list[str] = field(default_factory=list)
    host: str = ""
    port: int = 0
    status: str = "online"  # "online" | "busy" | "offline"
    registered_at: float = 0.0
    last_heartbeat: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRegistry:
    """Agent 注册表 — 基于 RedisManager.

    用法:
        registry = AgentRegistry(redis_manager)
        await registry.register("order-agent-1", "order", ["query_order", "track_shipping"])
        agents = await registry.discover(role="order")
    """

    def __init__(self, redis: Any) -> None:
        """Args: redis — RedisManager 实例."""
        self._redis = redis

    async def register(
        self,
        agent_id: str,
        role: str,
        capabilities: list[str] | None = None,
        host: str = "localhost",
        port: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """注册 Agent 到注册表."""
        import json
        now = time.time()
        info = {
            "agent_id": agent_id,
            "role": role,
            "capabilities": json.dumps(capabilities or []),
            "host": host,
            "port": str(port),
            "status": "online",
            "registered_at": str(now),
            "last_heartbeat": str(now),
            "metadata": json.dumps(metadata or {}),
        }
        r = self._redis._redis
        key = f"{REGISTRY_KEY_PREFIX}{agent_id}"
        await r.hset(key, mapping=info)
        await r.expire(key, REGISTRY_TTL)

        # 加入角色索引
        await r.sadd(f"{REGISTRY_KEY_PREFIX}role:{role}", agent_id)

        logger.info("Agent registered: %s (role=%s)", agent_id, role)

    async def heartbeat(self, agent_id: str) -> None:
        """心跳续期."""
        r = self._redis._redis
        key = f"{REGISTRY_KEY_PREFIX}{agent_id}"
        if await r.exists(key):
            await r.hset(key, "last_heartbeat", str(time.time()))
            await r.expire(key, REGISTRY_TTL)

    async def deregister(self, agent_id: str) -> None:
        """注销 Agent."""
        r = self._redis._redis
        key = f"{REGISTRY_KEY_PREFIX}{agent_id}"
        role = await r.hget(key, "role")
        await r.delete(key)
        if role:
            await r.srem(f"{REGISTRY_KEY_PREFIX}role:{role}", agent_id)
        logger.info("Agent deregistered: %s", agent_id)

    async def discover(
        self,
        role: str | None = None,
        capability: str | None = None,
    ) -> list[AgentInfo]:
        """发现在线 Agent."""
        import json
        r = self._redis._redis

        if role:
            agent_ids = await r.smembers(f"{REGISTRY_KEY_PREFIX}role:{role}")
        else:
            # 扫描所有 agent keys
            agent_ids = set()
            async for key in r.scan_iter(f"{REGISTRY_KEY_PREFIX}*"):
                k = key if isinstance(key, str) else key.decode()
                if ":role:" not in k and k.startswith(REGISTRY_KEY_PREFIX):
                    agent_ids.add(k.replace(REGISTRY_KEY_PREFIX, ""))

        results = []
        for aid in agent_ids:
            key = f"{REGISTRY_KEY_PREFIX}{aid}"
            data = await r.hgetall(key)
            if not data:
                continue

            caps = json.loads(data.get("capabilities", "[]"))
            if capability and capability not in caps:
                continue

            results.append(AgentInfo(
                agent_id=data.get("agent_id", aid),
                role=data.get("role", ""),
                capabilities=caps,
                host=data.get("host", ""),
                port=int(data.get("port", 0)),
                status=data.get("status", "offline"),
                registered_at=float(data.get("registered_at", 0)),
                last_heartbeat=float(data.get("last_heartbeat", 0)),
                metadata=json.loads(data.get("metadata", "{}")),
            ))

        return results

    async def set_status(self, agent_id: str, status: str) -> None:
        """更新 Agent 状态."""
        r = self._redis._redis
        key = f"{REGISTRY_KEY_PREFIX}{agent_id}"
        await r.hset(key, "status", status)

    async def get_info(self, agent_id: str) -> Optional[AgentInfo]:
        """获取单个 Agent 信息."""
        agents = await self.discover()
        for a in agents:
            if a.agent_id == agent_id:
                return a
        return None
