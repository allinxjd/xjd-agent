"""Credential 轮换管理 — API Key 自动切换 + 过期检测.

支持:
- 多 Key 池: 每个 provider 可配置多个 API Key
- 自动轮换: 429 限流时自动切换到下一个可用 Key
- 过期检测: 401/403 时标记 Key 为过期
- 冷却恢复: 限流 Key 在冷却期后自动恢复

用法:
    cm = CredentialManager()
    cm.add_keys("openai", ["sk-xxx", "sk-yyy"])
    key = cm.get_active_key("openai")
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)

class KeyState(str, Enum):
    """Key 状态."""
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    EXPIRED = "expired"
    DISABLED = "disabled"

@dataclass
class ManagedCredential:
    """受管理的凭证."""

    api_key: str
    provider: str
    state: KeyState = KeyState.ACTIVE
    fail_count: int = 0
    last_fail_time: float = 0.0
    rate_limit_until: float = 0.0
    total_requests: int = 0
    total_errors: int = 0

    @property
    def masked_key(self) -> str:
        if len(self.api_key) > 8:
            return self.api_key[:4] + "***" + self.api_key[-4:]
        return "***"

# 默认冷却时间 (秒)
_BASE_COOLDOWN = 60.0
_MAX_COOLDOWN = 3600.0

class CredentialManager:
    """凭证轮换管理器."""

    def __init__(self) -> None:
        self._pools: dict[str, list[ManagedCredential]] = {}
        self._lock = threading.Lock()

    def add_key(self, provider: str, api_key: str) -> None:
        """添加单个 Key."""
        if provider not in self._pools:
            self._pools[provider] = []
        # 去重
        for cred in self._pools[provider]:
            if cred.api_key == api_key:
                return
        self._pools[provider].append(ManagedCredential(api_key=api_key, provider=provider))

    def add_keys(self, provider: str, keys: list[str]) -> None:
        """批量添加 Key."""
        for k in keys:
            self.add_key(provider, k)

    def get_active_key(self, provider: str) -> Optional[str]:
        """获取当前可用的 Key.

        优先返回 ACTIVE 状态的 Key。
        RATE_LIMITED 的 Key 如果冷却期已过，自动恢复为 ACTIVE。
        """
        with self._lock:
            pool = self._pools.get(provider, [])
            if not pool:
                return None

            now = time.time()

            # 先尝试恢复冷却期已过的 Key
            for cred in pool:
                if cred.state == KeyState.RATE_LIMITED and now >= cred.rate_limit_until:
                    cred.state = KeyState.ACTIVE
                    cred.fail_count = 0
                    logger.info("Key %s 冷却期结束，已恢复", cred.masked_key)

            # 返回第一个 ACTIVE 的 Key
            for cred in pool:
                if cred.state == KeyState.ACTIVE:
                    return cred.api_key

            return None

    def report_error(self, provider: str, api_key: str, status_code: int) -> None:
        """报告 Key 错误，触发状态转换."""
        with self._lock:
            cred = self._find_credential(provider, api_key)
            if not cred:
                return

            cred.fail_count += 1
            cred.total_errors += 1
            cred.last_fail_time = time.time()

            if status_code == 429:
                # 限流 — 指数退避冷却
                cooldown = min(_BASE_COOLDOWN * (2 ** (cred.fail_count - 1)), _MAX_COOLDOWN)
                cred.state = KeyState.RATE_LIMITED
                cred.rate_limit_until = time.time() + cooldown
                logger.warning(
                    "Key %s 被限流，冷却 %.0f 秒", cred.masked_key, cooldown,
                )
            elif status_code in (401, 403):
                cred.state = KeyState.EXPIRED
                logger.error("Key %s 已过期/无效 (HTTP %d)", cred.masked_key, status_code)

    def report_success(self, provider: str, api_key: str) -> None:
        """报告 Key 成功使用."""
        cred = self._find_credential(provider, api_key)
        if not cred:
            return
        cred.total_requests += 1
        if cred.fail_count > 0:
            cred.fail_count = 0

    def get_stats(self) -> dict[str, dict[str, int]]:
        """获取各 provider 的 Key 统计."""
        stats: dict[str, dict[str, int]] = {}
        for provider, pool in self._pools.items():
            counts: dict[str, int] = {}
            for cred in pool:
                counts[cred.state.value] = counts.get(cred.state.value, 0) + 1
            counts["total"] = len(pool)
            stats[provider] = counts
        return stats

    def list_keys(self, provider: str) -> list[ManagedCredential]:
        """列出某 provider 的所有 Key."""
        return list(self._pools.get(provider, []))

    def disable_key(self, provider: str, api_key: str) -> bool:
        """手动禁用 Key."""
        cred = self._find_credential(provider, api_key)
        if cred:
            cred.state = KeyState.DISABLED
            return True
        return False

    def enable_key(self, provider: str, api_key: str) -> bool:
        """手动启用 Key."""
        cred = self._find_credential(provider, api_key)
        if cred:
            cred.state = KeyState.ACTIVE
            cred.fail_count = 0
            return True
        return False

    def _find_credential(self, provider: str, api_key: str) -> Optional[ManagedCredential]:
        for cred in self._pools.get(provider, []):
            if cred.api_key == api_key:
                return cred
        return None
