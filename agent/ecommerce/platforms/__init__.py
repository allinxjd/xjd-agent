"""平台适配器注册表 — 按名称查找平台实现."""

from __future__ import annotations

from typing import Optional, Type

from agent.ecommerce.base import EcommercePlatform

_REGISTRY: dict[str, Type[EcommercePlatform]] = {}


def register_platform(cls: Type[EcommercePlatform]) -> Type[EcommercePlatform]:
    """装饰器 — 注册平台适配器."""
    _REGISTRY[cls.platform_name] = cls
    return cls


def get_platform_class(name: str) -> Optional[Type[EcommercePlatform]]:
    """按名称获取平台类."""
    return _REGISTRY.get(name)


def list_platforms() -> list[str]:
    """列出所有已注册平台."""
    return list(_REGISTRY.keys())


# 自动导入所有平台模块以触发 @register_platform
def _auto_discover() -> None:
    import importlib
    import pkgutil
    import os

    pkg_dir = os.path.dirname(__file__)
    for _, modname, _ in pkgutil.iter_modules([pkg_dir]):
        if modname.startswith("_"):
            continue
        try:
            importlib.import_module(f"agent.ecommerce.platforms.{modname}")
        except Exception:
            pass


_auto_discover()
