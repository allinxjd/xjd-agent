"""多配置档管理 — 支持多个独立的 Agent 配置环境.

- 每个 profile 有独立的 config/memory/skills/sessions
- 支持 create/switch/list/export/import
- 默认 profile: "default"

用法:
    pm = ProfileManager()
    pm.create("work")
    pm.switch("work")
    pm.export("work", "/tmp/work-profile.tar.gz")
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class ProfileInfo:
    """配置档信息."""

    name: str
    path: Path
    is_active: bool = False
    created_at: str = ""
    description: str = ""

class ProfileManager:
    """多配置档管理器."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        from agent.core.config import get_home
        self._base = base_dir or get_home()
        self._profiles_dir = self._base / "profiles"
        self._profiles_dir.mkdir(exist_ok=True)
        self._active_file = self._base / ".active_profile"

    @property
    def active_profile(self) -> str:
        """当前激活的 profile 名称."""
        if self._active_file.exists():
            name = self._active_file.read_text().strip()
            if name and (self._profiles_dir / name).exists():
                return name
        return "default"

    def _profile_path(self, name: str) -> Path:
        return self._profiles_dir / name

    def create(self, name: str, description: str = "") -> bool:
        """创建新 profile."""
        p = self._profile_path(name)
        if p.exists():
            logger.warning("Profile %s 已存在", name)
            return False

        p.mkdir(parents=True)
        for sub in ("config", "memory", "skills", "sessions"):
            (p / sub).mkdir()

        meta = {"name": name, "description": description}
        (p / "profile.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

        logger.info("Profile %s 已创建", name)
        return True

    def switch(self, name: str) -> bool:
        """切换到指定 profile."""
        if name != "default" and not (self._profile_path(name)).exists():
            logger.error("Profile %s 不存在", name)
            return False
        self._active_file.write_text(name)
        logger.info("已切换到 profile: %s", name)
        return True

    def list_profiles(self) -> list[ProfileInfo]:
        """列出所有 profile."""
        profiles = [ProfileInfo(name="default", path=self._base, is_active=(self.active_profile == "default"))]
        if self._profiles_dir.exists():
            for d in sorted(self._profiles_dir.iterdir()):
                if d.is_dir() and (d / "profile.json").exists():
                    meta = json.loads((d / "profile.json").read_text(encoding="utf-8"))
                    profiles.append(ProfileInfo(
                        name=d.name,
                        path=d,
                        is_active=(self.active_profile == d.name),
                        description=meta.get("description", ""),
                    ))
        return profiles

    def delete(self, name: str) -> bool:
        """删除 profile."""
        if name == "default":
            logger.error("不能删除 default profile")
            return False
        p = self._profile_path(name)
        if not p.exists():
            return False
        shutil.rmtree(p)
        if self.active_profile == name:
            self.switch("default")
        return True

    def export_profile(self, name: str, output_path: str) -> bool:
        """导出 profile 为 tar.gz."""
        p = self._profile_path(name) if name != "default" else self._base
        if not p.exists():
            return False
        try:
            with tarfile.open(output_path, "w:gz") as tf:
                for sub in ("config", "memory", "skills"):
                    sub_path = p / sub
                    if sub_path.exists():
                        tf.add(str(sub_path), arcname=f"{name}/{sub}")
                meta = p / "profile.json"
                if meta.exists():
                    tf.add(str(meta), arcname=f"{name}/profile.json")
            return True
        except Exception as e:
            logger.error("导出失败: %s", e)
            return False

    def import_profile(self, archive_path: str, name: Optional[str] = None) -> bool:
        """从 tar.gz 导入 profile."""
        try:
            with tarfile.open(archive_path, "r:gz") as tf:
                members = tf.getnames()
                if not members:
                    return False
                # 推断 profile 名
                top = members[0].split("/")[0]
                target_name = name or top
                target = self._profile_path(target_name)
                target.mkdir(parents=True, exist_ok=True)

                with tempfile.TemporaryDirectory() as tmp:
                    tf.extractall(tmp, filter="data")
                    src = Path(tmp) / top
                    if src.exists():
                        for item in src.iterdir():
                            dest = target / item.name
                            if item.is_dir():
                                if dest.exists():
                                    shutil.rmtree(dest)
                                shutil.copytree(item, dest)
                            else:
                                shutil.copy2(item, dest)

                # 确保 profile.json 存在
                meta_file = target / "profile.json"
                if not meta_file.exists():
                    meta_file.write_text(json.dumps({"name": target_name}), encoding="utf-8")

            return True
        except Exception as e:
            logger.error("导入失败: %s", e)
            return False

    def get_profile_dir(self, subdir: str) -> Path:
        """获取当前 profile 的子目录."""
        name = self.active_profile
        if name == "default":
            d = self._base / subdir
        else:
            d = self._profile_path(name) / subdir
        d.mkdir(parents=True, exist_ok=True)
        return d
