"""XjdHub 客户端 — 技能打包/发布/安装/搜索."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SAFE_ID_RE = re.compile(r"^[^/\\\x00]{1,128}$")


@dataclass
class HubSkillInfo:
    name: str = ""
    version: str = ""
    author: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    price: float = 0.0
    downloads: int = 0


@dataclass
class InstallResult:
    success: bool = False
    skill_id: str = ""
    message: str = ""


@dataclass
class PublishResult:
    success: bool = False
    pkg_path: str = ""
    message: str = ""


class XjdHubClient:
    """XjdHub 技能市场客户端.

    现阶段使用本地 SQLite 索引模拟 Hub 后端。
    后续替换为远程 API (hub_url)。
    """

    def __init__(
        self,
        skill_manager: Any = None,
        hub_url: str = "",
    ) -> None:
        self._skill_manager = skill_manager
        self._hub_url = hub_url.rstrip("/") if hub_url else ""
        self._index = None
        from agent.core.config import get_home
        self._home = get_home()
        self._packages_dir = self._home / "packages"
        self._packages_dir.mkdir(parents=True, exist_ok=True)
        self._token_path = self._home / "hub_token.json"
        self._remote_token: str = ""
        self._load_token()

    @staticmethod
    def _validate_id(name: str, label: str = "ID") -> None:
        if not name or not _SAFE_ID_RE.match(name) or ".." in name:
            raise ValueError(f"非法{label}: {name!r}")

    @staticmethod
    def _safe_extract(tar: tarfile.TarFile, dest: str) -> None:
        """安全解压 tar，拒绝路径穿越和绝对路径."""
        dest_path = Path(dest).resolve()
        for member in tar.getmembers():
            member_path = (dest_path / member.name).resolve()
            if not member_path.is_relative_to(dest_path):
                raise ValueError(f"危险路径: {member.name}")
            if member.issym() or member.islnk():
                link_target = (dest_path / member.linkname).resolve()
                if not link_target.is_relative_to(dest_path):
                    raise ValueError(f"危险符号链接: {member.name} → {member.linkname}")
        tar.extractall(dest)

    async def initialize(self) -> None:
        from agent.hub.index import HubIndex
        self._index = HubIndex()
        await self._index.initialize()

    async def search(
        self, query: str = "", category: str = "", tags: list[str] | None = None,
    ) -> list[HubSkillInfo]:
        if not self._index:
            return []
        results = await self._index.search(query=query, category=category)
        return [
            HubSkillInfo(
                name=r.get("name", ""),
                version=r.get("version", ""),
                author=r.get("author", ""),
                description=r.get("description", ""),
                tags=r.get("tags", []),
                price=r.get("price", 0),
                downloads=r.get("downloads", 0),
            )
            for r in results
        ]

    async def pack(self, skill_id: str) -> str:
        """打包技能为 .xjdpkg (tar.gz)，返回文件路径."""
        if not self._skill_manager:
            raise ValueError("SkillManager 未设置")
        self._validate_id(skill_id, "skill_id")

        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            raise ValueError(f"技能不存在: {skill_id}")

        skill_dir = self._skill_manager._skills_dir / skill_id
        if not skill_dir.is_dir():
            raise ValueError(f"技能目录不存在: {skill_dir}")

        # 生成 manifest.json
        skill_md_path = skill_dir / "SKILL.md"
        checksum = ""
        if skill_md_path.exists():
            checksum = "sha256:" + hashlib.sha256(skill_md_path.read_bytes()).hexdigest()

        manifest = {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "author": skill.author,
            "license": "MIT",
            "price": skill.price,
            "tags": skill.tags,
            "category": skill.category,
            "dependencies": [],
            "min_agent_version": "0.1.0",
            "checksum": checksum,
            "published_at": time.time(),
        }

        # 写入 manifest.json 到技能目录
        manifest_path = skill_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

        # 打包为 tar.gz
        pkg_name = f"{skill_id}-{skill.version}.xjdpkg"
        pkg_path = self._packages_dir / pkg_name
        with tarfile.open(pkg_path, "w:gz") as tar:
            for item in skill_dir.iterdir():
                tar.add(item, arcname=item.name)

        # 清理临时 manifest
        manifest_path.unlink(missing_ok=True)

        logger.info("Packed skill %s → %s", skill_id, pkg_path)
        return str(pkg_path)

    async def unpack(self, pkg_path: str) -> Optional[Any]:
        """解包 .xjdpkg 到本地 skills 目录，返回 Skill."""
        if not self._skill_manager:
            raise ValueError("SkillManager 未设置")

        pkg = Path(pkg_path)
        if not pkg.exists():
            raise ValueError(f"包文件不存在: {pkg_path}")

        with tempfile.TemporaryDirectory() as tmpdir:
            with tarfile.open(pkg, "r:gz") as tar:
                self._safe_extract(tar, tmpdir)

            tmp = Path(tmpdir)
            skill_md = tmp / "SKILL.md"
            if not skill_md.exists():
                raise ValueError("包中缺少 SKILL.md")

            manifest_file = tmp / "manifest.json"
            manifest = {}
            if manifest_file.exists():
                manifest = json.loads(manifest_file.read_text())

            from agent.skills.manager import Skill
            content = skill_md.read_text()
            skill = Skill.from_skill_md(content)
            skill_name = manifest.get("name", skill.name)
            dir_name = re.sub(r"[^a-zA-Z0-9._-]", "-", skill_name.lower()).strip("-") or "unnamed"
            self._validate_id(dir_name, "skill dir_name")

            # 复制到 skills 目录
            target_dir = self._skill_manager._skills_dir / dir_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(tmpdir, target_dir)

            # 创建技能
            skill.skill_id = dir_name
            skill.source = "hub"
            skill.author = manifest.get("author", "")
            skill.price = manifest.get("price", 0)
            self._skill_manager._skills[dir_name] = skill
            await self._skill_manager._save_skill(skill)

            # 更新索引
            if self._index:
                manifest["installed_at"] = time.time()
                await self._index.add(manifest)
                await self._index.mark_installed(skill_name)

        logger.info("Installed skill from package: %s", skill.name)
        return skill

    async def install(self, skill_name: str, version: str = "latest") -> InstallResult:
        """从 Hub 安装技能 — 优先从 hub.db content 直装，fallback 到 .xjdpkg."""
        if not self._index:
            return InstallResult(success=False, message="Hub 索引未初始化")
        if not self._skill_manager:
            return InstallResult(success=False, message="SkillManager 未设置")

        content = await self._index.get_content(skill_name)
        if content:
            try:
                from agent.skills.manager import Skill
                if content.strip().startswith("---"):
                    skill = Skill.from_skill_md(content)
                else:
                    import yaml
                    data = yaml.safe_load(content)
                    if data and isinstance(data, dict):
                        skill = Skill.from_yaml_dict(data)
                    else:
                        return InstallResult(success=False, message="技能内容格式无效")
                if not skill.name:
                    return InstallResult(success=False, message="技能内容无效")
                dir_name = re.sub(r"[^a-zA-Z0-9._-]", "-", skill_name.lower()).strip("-") or skill_name
                self._validate_id(dir_name, "skill dir_name")
                target_dir = self._skill_manager._skills_dir / dir_name
                target_dir.mkdir(parents=True, exist_ok=True)
                save_content = content if content.strip().startswith("---") else skill.to_skill_md()
                (target_dir / "SKILL.md").write_text(save_content, encoding="utf-8")
                skill.skill_id = dir_name
                skill.source = "hub"
                self._skill_manager._skills[dir_name] = skill
                await self._skill_manager._save_skill(skill)
                await self._index.mark_installed(skill_name)
                return InstallResult(success=True, skill_id=dir_name, message=f"技能 {skill.name} 安装成功")
            except Exception as e:
                return InstallResult(success=False, message=f"安装失败: {e}")

        info = await self._index.get(skill_name)
        if not info:
            return InstallResult(success=False, message=f"技能 {skill_name} 不存在")

        pkg_name = f"{skill_name}-{info.get('version', '1.0.0')}.xjdpkg"
        pkg_path = self._packages_dir / pkg_name
        if not pkg_path.exists():
            return InstallResult(
                success=False,
                message=f"包文件不存在: {pkg_path}。请先让技能作者发布。",
            )

        try:
            skill = await self.unpack(str(pkg_path))
            return InstallResult(
                success=True,
                skill_id=skill.skill_id if skill else "",
                message=f"技能 {skill_name} 安装成功",
            )
        except Exception as e:
            return InstallResult(success=False, message=f"安装失败: {e}")

    async def publish(self, skill_id: str) -> PublishResult:
        """发布技能到 Hub（现阶段打包 + 写入本地索引）."""
        try:
            pkg_path = await self.pack(skill_id)
        except Exception as e:
            return PublishResult(success=False, message=f"打包失败: {e}")

        # 读取 manifest
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return PublishResult(success=False, message="技能不存在")

        manifest = {
            "name": skill.name,
            "version": skill.version,
            "description": skill.description,
            "author": skill.author,
            "tags": skill.tags,
            "category": skill.category,
            "price": skill.price,
            "downloads": 0,
            "published_at": time.time(),
        }

        if self._index:
            await self._index.add(manifest)

        # 更新技能的 hub 状态
        skill.hub_id = skill.skill_id
        from agent.skills.manager import SkillManager
        SkillManager._log_evolution(skill, "published", f"v{skill.version}")
        await self._skill_manager._save_skill(skill)

        logger.info("Published skill %s to Hub", skill.name)
        return PublishResult(success=True, pkg_path=pkg_path, message=f"技能 {skill.name} 已发布")

    # ── Remote Hub API (支付/账户) ──────────────────────────────

    def _load_token(self) -> None:
        if self._token_path.exists():
            try:
                data = json.loads(self._token_path.read_text())
                self._remote_token = data.get("token", "")
            except Exception:
                pass

    def _save_token(self, token: str, user_id: str = "", username: str = "") -> None:
        self._remote_token = token
        self._token_path.write_text(json.dumps({
            "token": token, "user_id": user_id, "username": username,
        }, ensure_ascii=False))

    def _remote_headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._remote_token:
            h["Authorization"] = f"Bearer {self._remote_token}"
        return h

    async def _remote_get(self, path: str) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as c:
            resp = await c.get(f"{self._hub_url}{path}", headers=self._remote_headers())
            return resp.json()

    async def _remote_post(self, path: str, body: dict) -> dict:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as c:
            resp = await c.post(f"{self._hub_url}{path}", json=body, headers=self._remote_headers())
            return resp.json()

    async def remote_register(self, username: str, email: str, password: str) -> dict:
        data = await self._remote_post("/hub/api/auth/register", {
            "username": username, "email": email, "password": password,
        })
        if data.get("token"):
            self._save_token(data["token"], data.get("user_id", ""), username)
        return data

    async def remote_login(self, username: str, password: str) -> dict:
        data = await self._remote_post("/hub/api/auth/login", {
            "username": username, "password": password,
        })
        if data.get("token"):
            self._save_token(data["token"], data.get("user_id", ""), username)
        return data

    async def remote_balance(self) -> dict:
        return await self._remote_get("/hub/api/auth/me")

    async def remote_recharge_packages(self) -> dict:
        return await self._remote_get("/hub/api/recharge/packages")

    async def remote_recharge_create(self, amount: float, pay_type: str = "native") -> dict:
        return await self._remote_post("/hub/api/recharge/create", {
            "amount": amount, "pay_type": pay_type,
        })

    async def remote_recharge_status(self, order_no: str) -> dict:
        return await self._remote_get(f"/hub/api/recharge/status/{order_no}")

    @property
    def has_remote_token(self) -> bool:
        return bool(self._remote_token)

    async def close(self) -> None:
        if self._index:
            await self._index.close()
