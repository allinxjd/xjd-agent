"""技能级凭证存储 — 独立于全局 config，按技能隔离.

存储位置: ~/.xjd-agent/secrets.yaml (权限 0o600)
格式:
  ecommerce-image-pipeline:
    CALABASH_PHONE: "138..."
    CALABASH_PASSWORD: "xxx"
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_instance: Optional[SecretsStore] = None


def _secrets_path() -> Path:
    from agent.core.config import get_home
    return get_home() / "secrets.yaml"


def get_secrets_store() -> SecretsStore:
    """获取全局 SecretsStore 单例."""
    global _instance
    if _instance is None:
        _instance = SecretsStore()
    return _instance


class SecretsStore:

    def __init__(self, path: Path | None = None):
        self._path = path or _secrets_path()
        self._data: dict[str, dict[str, str]] = {}
        self._load()
        self._migrate_calabash()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    raw = yaml.safe_load(f) or {}
                self._data = {k: dict(v) for k, v in raw.items() if isinstance(v, dict)}
            except Exception as e:
                logger.warning("Failed to load secrets: %s", e)
                self._data = {}

    def _save(self):
        tmp = self._path.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                yaml.dump(self._data, f, default_flow_style=False, allow_unicode=True)
            os.chmod(tmp, 0o600)
            tmp.replace(self._path)
            os.chmod(self._path, 0o600)
        except Exception as e:
            logger.error("Failed to save secrets: %s", e)
            tmp.unlink(missing_ok=True)

    def get(self, skill_id: str, key: str, default: str = "") -> str:
        return self._data.get(skill_id, {}).get(key, default)

    def get_all(self, skill_id: str) -> dict[str, str]:
        return dict(self._data.get(skill_id, {}))

    def set(self, skill_id: str, key: str, value: str):
        self._data.setdefault(skill_id, {})[key] = value
        self._save()

    def set_bulk(self, skill_id: str, secrets: dict[str, str]):
        self._data.setdefault(skill_id, {}).update(secrets)
        self._save()

    def delete_skill(self, skill_id: str):
        if self._data.pop(skill_id, None) is not None:
            self._save()

    def list_skills(self) -> list[str]:
        return list(self._data.keys())

    def _migrate_calabash(self):
        """从 config.yaml 的 calabash 节迁移到 secrets.yaml."""
        if self._data.get("ecommerce-image-pipeline"):
            return
        from agent.core.config import get_config_path
        config_path = get_config_path()
        if not config_path.exists():
            return
        try:
            with open(config_path) as f:
                data = yaml.safe_load(f) or {}
            cb = data.get("calabash")
            if not cb or not isinstance(cb, dict):
                return
            phone = cb.get("phone", "")
            password = cb.get("password", "")
            if not phone and not password:
                return
            self.set_bulk("ecommerce-image-pipeline", {
                "CALABASH_PHONE": phone,
                "CALABASH_PASSWORD": password,
                "CALABASH_API_URL": cb.get("api_url", "https://ai.allinxjd.com"),
            })
            data.pop("calabash", None)
            with open(config_path, "w") as f:
                yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            logger.info("Migrated calabash credentials to secrets.yaml")
        except Exception as e:
            logger.warning("Calabash migration failed: %s", e)
