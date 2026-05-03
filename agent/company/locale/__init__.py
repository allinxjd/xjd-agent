"""CompanyLocale — i18n for AI Company."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_LOCALE_DIR = Path(__file__).parent
_cache: dict[str, "CompanyLocale"] = {}


class CompanyLocale:
    """Dot-notation key lookup with fallback to zh-CN."""

    def __init__(self, data: dict[str, Any], fallback: Optional["CompanyLocale"] = None) -> None:
        self._data = data
        self._fallback = fallback

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        node: Any = self._data
        for p in parts:
            if isinstance(node, dict):
                node = node.get(p)
            else:
                node = None
            if node is None:
                break
        if node is not None:
            return node
        if self._fallback:
            return self._fallback.get(key, default)
        return default

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def format(self, key: str, **kwargs: Any) -> str:
        template = self.get(key, "")
        if not template:
            return ""
        try:
            return str(template).format(**kwargs)
        except (KeyError, IndexError):
            return str(template)

    @classmethod
    def load(cls, locale_name: str = "zh-CN") -> "CompanyLocale":
        if locale_name in _cache:
            return _cache[locale_name]

        import yaml

        fallback = None
        if locale_name != "zh-CN":
            fallback = cls.load("zh-CN")

        path = _LOCALE_DIR / f"{locale_name}.yaml"
        if not path.exists():
            logger.warning("Locale file not found: %s, falling back to zh-CN", path)
            if fallback:
                _cache[locale_name] = fallback
                return fallback
            path = _LOCALE_DIR / "zh-CN.yaml"

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        instance = cls(data, fallback=fallback)
        _cache[locale_name] = instance
        return instance

    @classmethod
    def clear_cache(cls) -> None:
        _cache.clear()
