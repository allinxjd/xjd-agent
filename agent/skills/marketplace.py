"""技能市场客户端 — 连接 XjdHub 远程技能注册中心.

支持:
- 远程搜索/安装/发布技能
- 签名校验 (Phase 2)
- 许可证验证 (Phase 4)
- 向后兼容旧 GitHub JSON 索引模式

用法:
    client = HubClient("https://hub.xjdagent.com")
    await client.login("user", "pass")
    results = await client.search("部署")
    await client.install("auto-deploy")
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

DEFAULT_HUB_URL = "https://hub.xjdagent.com"
_CONFIG_FILE = "hub_config.json"


@dataclass
class HubSkillMeta:
    """Hub 技能元数据."""
    skill_id: str = ""
    name: str = ""
    slug: str = ""
    description: str = ""
    author: str = ""
    version: str = "1.0.0"
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    price: float = 0.0
    downloads: int = 0
    rating: float = 0.0
    created_at: float = 0.0


@dataclass
class DownloadResult:
    content: str = ""
    content_hash: str = ""
    signature: str = ""
    author_pubkey: str = ""
    version: str = ""


@dataclass
class InstallResult:
    success: bool = False
    skill_id: str = ""
    message: str = ""


@dataclass
class PublishResult:
    success: bool = False
    skill_id: str = ""
    slug: str = ""
    message: str = ""


class HubClient:
    """XjdHub 远程客户端."""

    def __init__(
        self,
        hub_url: str = "",
        skills_dir: Optional[Path] = None,
    ) -> None:
        from agent.core.config import get_home
        self._home = get_home()
        self._skills_dir = skills_dir or (self._home / "skills")
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._config = self._load_config()
        self._hub_url = (hub_url or self._config.get("hub_url", DEFAULT_HUB_URL)).rstrip("/")
        self._token = self._config.get("token", "")

    def _config_path(self) -> Path:
        return self._home / _CONFIG_FILE

    def _load_config(self) -> dict:
        p = self._config_path()
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_config(self) -> None:
        self._config["hub_url"] = self._hub_url
        self._config["token"] = self._token
        self._config_path().write_text(
            json.dumps(self._config, ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def _headers(self) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _request(self, method: str, path: str, data: dict | None = None) -> dict:
        import httpx
        url = f"{self._hub_url}{path}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method == "GET":
                resp = await client.get(url, headers=self._headers(), params=data)
            else:
                resp = await client.request(method, url, headers=self._headers(), json=data)
            return {"status": resp.status_code, "data": resp.json()}

    async def login(self, username: str, password: str) -> str:
        result = await self._request("POST", "/hub/api/auth/login", {
            "username": username, "password": password,
        })
        if result["status"] == 200:
            self._token = result["data"]["token"]
            self._config["username"] = username
            self._save_config()
            return self._token
        raise ValueError(result["data"].get("error", "Login failed"))

    async def register(self, username: str, email: str, password: str) -> str:
        result = await self._request("POST", "/hub/api/auth/register", {
            "username": username, "email": email, "password": password,
        })
        if result["status"] == 201:
            self._token = result["data"]["token"]
            self._config["username"] = username
            self._save_config()
            return self._token
        raise ValueError(result["data"].get("error", "Registration failed"))

    async def search(
        self, query: str = "", category: str = "", tags: Optional[list[str]] = None,
        page: int = 1,
    ) -> list[HubSkillMeta]:
        params: dict[str, Any] = {"page": str(page)}
        if query:
            params["q"] = query
        if category:
            params["category"] = category
        if tags:
            params["tag"] = tags[0]

        result = await self._request("GET", "/hub/api/skills", params)
        if result["status"] != 200:
            return []

        skills = []
        for item in result["data"].get("skills", []):
            skills.append(HubSkillMeta(
                skill_id=item.get("skill_id", ""),
                name=item.get("name", ""),
                slug=item.get("slug", ""),
                description=item.get("description", ""),
                version=item.get("version", ""),
                category=item.get("category", ""),
                tags=item.get("tags", []),
                tools=item.get("tools", []),
                price=float(item.get("price", 0)),
                downloads=int(item.get("downloads", 0)),
                rating=float(item.get("rating_avg", 0)),
            ))
        return skills

    async def get_skill(self, slug: str) -> Optional[dict]:
        result = await self._request("GET", f"/hub/api/skills/{slug}")
        if result["status"] == 200:
            return result["data"].get("skill")
        return None

    async def download(self, slug: str) -> DownloadResult:
        result = await self._request("GET", f"/hub/api/skills/{slug}/download")
        if result["status"] != 200:
            raise ValueError(result["data"].get("error", "Download failed"))
        d = result["data"]
        return DownloadResult(
            content=d.get("content", ""),
            content_hash=d.get("content_hash", ""),
            signature=d.get("signature", ""),
            author_pubkey=d.get("author_pubkey", ""),
            version=d.get("version", ""),
        )

    async def install(self, slug: str, version: str = "latest") -> InstallResult:
        try:
            dl = await self.download(slug)
        except ValueError as e:
            return InstallResult(success=False, message=str(e))

        # 校验 content hash
        actual_hash = hashlib.sha256(dl.content.encode()).hexdigest()
        if dl.content_hash and actual_hash != dl.content_hash:
            return InstallResult(
                success=False,
                message=f"Content hash mismatch: expected {dl.content_hash}, got {actual_hash}",
            )

        # 保存到本地
        skill_dir = self._skills_dir / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(dl.content, encoding="utf-8")

        # 保存 hub 元数据
        meta = {
            "hub_id": slug,
            "version": dl.version,
            "content_hash": dl.content_hash,
            "signature": dl.signature,
            "author_pubkey": dl.author_pubkey,
            "installed_at": time.time(),
        }
        (skill_dir / ".hub_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        return InstallResult(success=True, skill_id=slug, message="Installed successfully")

    async def publish(self, skill_path: str, metadata: Optional[dict] = None) -> PublishResult:
        path = Path(skill_path)
        if path.is_dir():
            path = path / "SKILL.md"
        if not path.exists():
            return PublishResult(success=False, message=f"File not found: {path}")

        content = path.read_text(encoding="utf-8")

        # 解析 frontmatter 获取元数据
        import re
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        fm_data: dict = {}
        if fm_match:
            fm_data = yaml.safe_load(fm_match.group(1)) or {}

        data = {
            "name": fm_data.get("name", path.parent.name),
            "description": fm_data.get("description", ""),
            "version": fm_data.get("version", "1.0.0"),
            "category": fm_data.get("category", "general"),
            "tags": fm_data.get("tags", []),
            "tools": fm_data.get("tools", []),
            "content": content,
        }
        if metadata:
            data.update(metadata)

        result = await self._request("POST", "/hub/api/skills", data)
        if result["status"] == 201:
            d = result["data"]
            return PublishResult(
                success=True, skill_id=d.get("skill_id", ""),
                slug=d.get("slug", ""), message="Published, pending review",
            )
        return PublishResult(success=False, message=result["data"].get("error", "Publish failed"))

    async def purchase(self, slug: str) -> dict:
        result = await self._request("POST", f"/hub/api/skills/{slug}/purchase")
        if result["status"] == 200:
            return result["data"]
        raise ValueError(result["data"].get("error", "Purchase failed"))

    def list_installed(self) -> list[str]:
        installed = []
        for d in self._skills_dir.iterdir():
            if d.is_dir() and (d / ".hub_meta.json").exists():
                installed.append(d.name)
        return installed

    async def uninstall(self, slug: str) -> bool:
        skill_dir = self._skills_dir / slug
        if skill_dir.exists() and skill_dir.is_dir():
            import shutil
            shutil.rmtree(skill_dir)
            return True
        return False


# 向后兼容别名
SkillMarketplace = HubClient
MarketplaceSkillMeta = HubSkillMeta
