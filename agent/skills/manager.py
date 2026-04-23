"""技能管理器 — SKILL.md 格式.

技能体系:
1. 技能 = SKILL.md 文件 (YAML frontmatter + Markdown body)
2. 技能存储在 ~/.xjd-agent/skills/<name>/SKILL.md
3. Agent 遇到类似任务时，自动匹配并应用已有技能
4. 支持渐进式披露: tier 1 (元数据) → tier 2 (完整内容)
5. 向后兼容旧 YAML 格式 (自动迁移)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# ── YAML frontmatter 解析 ──

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)
_SAFE_SKILL_ID_RE = re.compile(r"^[^/\\\x00]{1,128}$")


def _validate_skill_id(skill_id: str) -> None:
    """校验 skill_id 不含路径穿越字符."""
    if not skill_id or not _SAFE_SKILL_ID_RE.match(skill_id) or ".." in skill_id:
        raise ValueError(f"非法 skill_id: {skill_id!r}")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 YAML frontmatter + Markdown body."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2).strip()
    return fm, body


def _render_frontmatter(meta: dict[str, Any], body: str) -> str:
    """渲染 YAML frontmatter + Markdown body."""
    fm = yaml.dump(meta, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{fm}\n---\n\n{body}\n"

@dataclass
class SkillSecret:
    """技能所需的凭证声明."""
    key: str = ""
    description: str = ""
    default: str = ""


@dataclass
class Skill:
    """技能定义 — 兼容 SKILL.md 和旧 YAML 格式."""

    skill_id: str = ""
    name: str = ""
    description: str = ""
    trigger: str = ""
    category: str = "general"
    version: str = "1.0.0"

    # Markdown body (SKILL.md 的正文部分)
    body: str = ""

    # AgentSkills 标准字段
    tools: list[str] = field(default_factory=list)  # 允许使用的工具白名单
    secrets: list[SkillSecret] = field(default_factory=list)

    # 旧格式兼容: 结构化步骤
    steps: list[dict[str, Any]] = field(default_factory=list)

    # 元数据
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    prerequisites: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    created_at: float = 0.0
    updated_at: float = 0.0
    use_count: int = 0
    success_rate: float = 1.0
    failure_count: int = 0
    deprecated: bool = False

    # XjdHub / 状态管理
    status: str = "active"              # draft / active / deprecated
    source: str = "manual"              # manual / chat / hub / auto_extracted
    author: str = ""
    price: float = 0.0                  # 0 = 免费
    hub_id: str = ""                    # XjdHub 远程 ID
    downloads: int = 0

    # 版本管理
    versions: list[dict[str, Any]] = field(default_factory=list)
    # [{version, body, updated_at, changelog}]

    # 进化日志
    evolution_log: list[dict[str, Any]] = field(default_factory=list)
    # [{timestamp, event, details}]

    # ── Tier 1: 元数据 (用于技能列表 / system prompt 概览) ──

    def to_metadata(self) -> dict[str, Any]:
        """返回 tier 1 元数据."""
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "trigger": self.trigger,
            "use_count": self.use_count,
            "success_rate": self.success_rate,
            "version": self.version,
            "status": self.status,
            "source": self.source,
            "author": self.author,
            "price": self.price,
            "downloads": self.downloads,
        }

    # ── Tier 2: 完整内容 (匹配后注入) ──

    def to_full_content(self) -> str:
        """返回完整 SKILL.md 内容 (用于注入 user message)."""
        if self.body:
            return self.body
        # 旧格式回退: 从 steps 生成
        return self._steps_to_markdown()

    def _steps_to_markdown(self) -> str:
        """将旧格式 steps 转为 Markdown."""
        if not self.steps:
            return ""
        lines = [f"# {self.name}", "", "## 使用流程", ""]
        for i, step in enumerate(self.steps, 1):
            desc = step.get("description", "")
            tool = step.get("tool")
            if tool:
                lines.append(f"{i}. {desc} (使用工具: `{tool}`)")
            else:
                lines.append(f"{i}. {desc}")
        return "\n".join(lines)

    def to_skill_md(self) -> str:
        """序列化为 SKILL.md 格式 (兼容 AgentSkills 标准)."""
        meta: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
        }
        # AgentSkills 标准: tools 字段 (工具白名单)
        tools_list = self.tools or list({s.get("tool") for s in self.steps if s.get("tool")})
        if tools_list:
            meta["tools"] = tools_list
        # 扩展字段
        if self.category != "general":
            meta["category"] = self.category
        if self.tags:
            meta["tags"] = self.tags
        if self.trigger:
            meta["trigger"] = self.trigger
        if self.examples:
            meta["examples"] = self.examples
        if self.prerequisites:
            meta["prerequisites"] = self.prerequisites
        if self.secrets:
            meta["secrets"] = [
                {"key": s.key, "description": s.description, **({"default": s.default} if s.default else {})}
                for s in self.secrets
            ]
        # 运行时统计 (非标准，xjd 扩展)
        xjd_meta: dict[str, Any] = {}
        if self.use_count or self.success_rate < 1.0:
            xjd_meta["success_rate"] = self.success_rate
            xjd_meta["use_count"] = self.use_count
        if self.status != "active":
            meta["status"] = self.status
        if self.source != "manual":
            meta["source"] = self.source
        if self.author:
            meta["author"] = self.author
        if self.price > 0:
            meta["price"] = self.price
        if self.hub_id:
            meta["hub_id"] = self.hub_id
        if self.downloads > 0:
            xjd_meta["downloads"] = self.downloads
        if self.versions:
            xjd_meta["versions"] = self.versions[-10:]
        if self.evolution_log:
            xjd_meta["evolution_log"] = self.evolution_log[-10:]
        if xjd_meta:
            meta["metadata"] = xjd_meta
        body = self.body or self._steps_to_markdown()
        return _render_frontmatter(meta, body)

    @classmethod
    def from_skill_md(cls, text: str, skill_id: str = "") -> Skill:
        """从 SKILL.md 内容解析 (兼容 AgentSkills 标准)."""
        fm, body = _parse_frontmatter(text)
        meta = fm.get("metadata", {})
        # tools 可以在顶层或 metadata 里
        tools = fm.get("tools", meta.get("tools", []))
        raw_secrets = fm.get("secrets", [])
        secrets = [
            SkillSecret(key=s.get("key", ""), description=s.get("description", ""), default=s.get("default", ""))
            for s in raw_secrets if isinstance(s, dict)
        ]
        return cls(
            skill_id=skill_id or fm.get("name", str(uuid.uuid4())[:8]),
            name=fm.get("name", ""),
            description=fm.get("description", ""),
            trigger=fm.get("trigger", ""),
            category=fm.get("category", "general"),
            version=str(fm.get("version", "1.0.0")),
            body=body,
            tools=tools if isinstance(tools, list) else [],
            secrets=secrets,
            tags=fm.get("tags", []),
            examples=fm.get("examples", []),
            prerequisites=fm.get("prerequisites", {}),
            metadata=meta,
            use_count=meta.get("use_count", 0),
            success_rate=meta.get("success_rate", 1.0),
            status=fm.get("status", "active"),
            source=fm.get("source", "manual"),
            author=fm.get("author", ""),
            price=float(fm.get("price", 0)),
            hub_id=fm.get("hub_id", ""),
            downloads=meta.get("downloads", 0),
            versions=meta.get("versions", []),
            evolution_log=meta.get("evolution_log", []),
        )

    @classmethod
    def from_yaml_dict(cls, data: dict[str, Any]) -> Skill:
        """从旧 YAML 格式解析 (向后兼容)."""
        return cls(
            skill_id=data.get("skill_id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            trigger=data.get("trigger", ""),
            category=data.get("category", "general"),
            steps=data.get("steps", []),
            tags=data.get("tags", []),
            examples=data.get("examples", []),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            version=str(data.get("version", "1.0.0")),
            use_count=data.get("use_count", 0),
            success_rate=data.get("success_rate", 1.0),
            failure_count=data.get("failure_count", 0),
            deprecated=data.get("deprecated", False),
        )

    def to_prompt(self) -> str:
        """生成注入的技能描述 (tier 1 概览)."""
        return f"- {self.name}: {self.description}"

    # 兼容旧代码
    def to_dict(self) -> dict[str, Any]:
        return self.to_metadata()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Skill:
        return cls.from_yaml_dict(data)

# ── Prompts ──

SKILL_EXTRACTION_PROMPT = """分析以下成功完成的任务对话，提取可复用的技能。

提取规则:
1. 只提取包含工具调用的、多步骤的任务流程
2. 简单问答不需要提取
3. 技能应该是通用可复用的 (不要包含具体的文件名/路径等)

返回 SKILL.md 格式 (如果没有值得提取的技能，返回 null):

```markdown
---
name: 技能名称
description: 一句话描述
version: 1.0.0
category: general|code|file|web|data|deploy
tags: [标签1, 标签2]
trigger: 什么情况下应该使用这个技能
examples:
  - 触发示例1
  - 触发示例2
metadata:
  tools: [用到的工具名]
---

# 技能名称

## 使用流程

1. 第一步描述
2. 第二步描述 (使用工具: `tool_name`)
3. ...

## 注意事项

- 注意点1
- 注意点2
```

任务对话:
{conversation}

请分析并返回 SKILL.md 格式 (或 null):"""

SKILL_MATCHING_PROMPT = """用户的请求:
{user_message}

以下是可用的技能列表:
{skills_list}

请判断是否有匹配的技能。如果有，返回技能 ID。如果没有，返回 null。

返回 JSON:
{{"matched_skill_id": "xxx" 或 null, "confidence": 0.0-1.0, "reason": "匹配原因"}}"""


class SkillManager:
    """技能管理器 — 支持 SKILL.md 格式 + 旧 YAML 兼容.

    目录结构:
        ~/.xjd-agent/skills/
        ├── ecommerce-image/
        │   └── SKILL.md
        ├── deploy-server/
        │   └── SKILL.md
        └── old-skill-id.yaml  (旧格式，启动时自动迁移)
    """

    def __init__(self, skills_dir: Optional[str] = None) -> None:
        if skills_dir:
            self._skills_dir = Path(skills_dir)
        else:
            from agent.core.config import get_skills_dir
            self._skills_dir = get_skills_dir()

        self._skills: dict[str, Skill] = {}
        self._loaded = False
        self._last_loaded: float = 0.0
        self._lock = asyncio.Lock()  # 并发安全
        self._procedural_bridge: Optional[Any] = None

    def set_procedural_bridge(self, bridge: Any) -> None:
        """设置程序记忆桥接器."""
        self._procedural_bridge = bridge

    async def load_skills(self) -> int:
        """从磁盘加载所有技能 (SKILL.md 优先，YAML 兼容).

        Returns:
            加载的技能数量
        """
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        count = 0

        # 1. 加载新格式: <name>/SKILL.md
        for skill_dir in self._skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            try:
                text = skill_md.read_text(encoding="utf-8")
                skill = Skill.from_skill_md(text, skill_id=skill_dir.name)
                self._skills[skill.skill_id] = skill
                count += 1
            except (OSError, yaml.YAMLError, ValueError) as e:
                logger.warning("Failed to load SKILL.md from %s: %s", skill_dir.name, e)

        # 2. 加载旧格式: *.yaml (并自动迁移)
        for path in self._skills_dir.glob("*.yaml"):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                skill = Skill.from_yaml_dict(data)
                if not skill.skill_id:
                    skill.skill_id = path.stem
                # 跳过已被新格式覆盖的
                if skill.skill_id in self._skills:
                    continue
                self._skills[skill.skill_id] = skill
                count += 1
                # 自动迁移到 SKILL.md
                await self._migrate_yaml_to_md(skill, path)
            except (OSError, yaml.YAMLError, ValueError) as e:
                logger.warning("Failed to load skill %s: %s", path.name, e)

        self._loaded = True
        logger.info("Loaded %d skills from %s", count, self._skills_dir)
        return count

    async def _migrate_yaml_to_md(self, skill: Skill, yaml_path: Path) -> None:
        """将旧 YAML 技能迁移为 SKILL.md 目录格式."""
        try:
            # 用 name 做目录名 (slug 化)
            dir_name = re.sub(r"[^a-zA-Z0-9._-]", "-", skill.name.lower()).strip("-")
            if not dir_name:
                dir_name = skill.skill_id
            _validate_skill_id(dir_name)
            skill_dir = self._skills_dir / dir_name
            skill_dir.mkdir(exist_ok=True)

            # 写 SKILL.md
            md_path = skill_dir / "SKILL.md"
            md_path.write_text(skill.to_skill_md(), encoding="utf-8")

            # 备份旧文件
            backup = yaml_path.with_suffix(".yaml.bak")
            shutil.move(str(yaml_path), str(backup))

            # 更新 skill_id
            old_id = skill.skill_id
            skill.skill_id = dir_name
            if old_id in self._skills:
                del self._skills[old_id]
            self._skills[dir_name] = skill

            logger.info("Migrated skill %s → %s/SKILL.md", yaml_path.name, dir_name)
        except (OSError, yaml.YAMLError) as e:
            logger.warning("Migration failed for %s: %s", yaml_path.name, e)

    async def _ensure_loaded(self) -> None:
        if not self._loaded or (time.time() - self._last_loaded > 300):
            await self.load_skills()
            self._last_loaded = time.time()

    # ── CRUD ──

    async def create_skill(
        self,
        name: str,
        description: str,
        trigger: str,
        steps: list[dict[str, Any]] | None = None,
        body: str = "",
        category: str = "general",
        tags: list[str] | None = None,
        examples: list[str] | None = None,
        source: str = "manual",
        author: str = "",
        status: str = "active",
    ) -> Skill:
        """创建新技能 (SKILL.md 格式)."""
        await self._ensure_loaded()

        async with self._lock:
            dir_name = re.sub(r"[^a-zA-Z0-9._-]", "-", name.lower()).strip("-") or str(uuid.uuid4())[:8]
            _validate_skill_id(dir_name)
            skill = Skill(
                skill_id=dir_name,
                name=name,
                description=description,
                trigger=trigger,
                category=category,
                body=body,
                steps=steps or [],
                tags=tags or [],
                examples=examples or [],
                created_at=time.time(),
                updated_at=time.time(),
                source=source,
                author=author,
                status=status,
            )
            self._log_evolution(skill, "created", f"来源: {source}")

            self._skills[skill.skill_id] = skill
            await self._save_skill_unlocked(skill)

        if self._procedural_bridge:
            try:
                await self._procedural_bridge.on_skill_created(skill)
            except (AttributeError, TypeError, OSError) as e:
                logger.debug("Procedural bridge sync failed: %s", e)

        logger.info("Created skill: %s (%s)", skill.name, skill.skill_id)
        return skill

    async def update_skill(self, skill_id: str, updates: dict[str, Any]) -> Optional[Skill]:
        """更新技能."""
        await self._ensure_loaded()
        async with self._lock:
            skill = self._skills.get(skill_id)
            if not skill:
                return None

            for key, value in updates.items():
                if hasattr(skill, key):
                    setattr(skill, key, value)

            skill.updated_at = time.time()
            await self._save_skill_unlocked(skill)

        if self._procedural_bridge:
            try:
                await self._procedural_bridge.on_skill_updated(skill)
            except (AttributeError, TypeError, OSError) as e:
                logger.debug("Procedural bridge update failed: %s", e)

        logger.info("Updated skill: %s", skill.name)
        return skill

    async def delete_skill(self, skill_id: str) -> bool:
        """删除技能."""
        _validate_skill_id(skill_id)
        await self._ensure_loaded()
        async with self._lock:
            skill = self._skills.pop(skill_id, None)
            if not skill:
                return False

            # 删除 SKILL.md 目录
            skill_dir = self._skills_dir / skill_id
            if skill_dir.is_dir():
                shutil.rmtree(skill_dir)
            # 兼容旧格式
            yaml_path = self._skills_dir / f"{skill_id}.yaml"
            if yaml_path.exists():
                yaml_path.unlink()

        if self._procedural_bridge:
            try:
                await self._procedural_bridge.on_skill_deleted(skill_id, skill.name)
            except (AttributeError, TypeError, OSError) as e:
                logger.debug("Procedural bridge delete failed: %s", e)

        logger.info("Deleted skill: %s", skill.name)
        return True

    # ── 版本管理 ──

    async def save_version(self, skill_id: str, changelog: str = "") -> str:
        """保存当前版本快照，返回版本号."""
        await self._ensure_loaded()
        async with self._lock:
            skill = self._skills.get(skill_id)
            if not skill:
                return ""
            snapshot = {
                "version": skill.version,
                "body": skill.body,
                "trigger": skill.trigger,
                "updated_at": skill.updated_at,
                "changelog": changelog,
            }
            skill.versions.append(snapshot)
            if len(skill.versions) > 10:
                skill.versions = skill.versions[-10:]
            self._log_evolution(skill, "version_saved", changelog or f"v{skill.version}")
            await self._save_skill_unlocked(skill)
        return skill.version

    async def rollback_version(self, skill_id: str, version: str) -> Optional[Skill]:
        """回滚到指定版本."""
        await self._ensure_loaded()
        async with self._lock:
            skill = self._skills.get(skill_id)
            if not skill:
                return None
            target = None
            for v in skill.versions:
                if v.get("version") == version:
                    target = v
                    break
            if not target:
                return None
            # rollback 前自动备份
            snapshot = {
                "version": skill.version,
                "body": skill.body,
                "trigger": skill.trigger,
                "updated_at": skill.updated_at,
                "changelog": f"rollback 前自动备份 (from v{skill.version})",
            }
            skill.versions.append(snapshot)
            if len(skill.versions) > 10:
                skill.versions = skill.versions[-10:]
            # 执行回滚
            skill.body = target.get("body", skill.body)
            skill.trigger = target.get("trigger", skill.trigger)
            skill.version = target.get("version", skill.version)
            skill.updated_at = time.time()
            self._log_evolution(skill, "rollback", f"回滚到 v{version}")
            await self._save_skill_unlocked(skill)
        logger.info("Rolled back skill %s to v%s", skill.name, version)
        return skill

    async def list_versions(self, skill_id: str) -> list[dict[str, Any]]:
        """列出版本历史."""
        await self._ensure_loaded()
        skill = self._skills.get(skill_id)
        if not skill:
            return []
        return list(skill.versions)

    @staticmethod
    def _log_evolution(skill: Skill, event: str, details: str = "") -> None:
        skill.evolution_log.append({
            "timestamp": time.time(),
            "event": event,
            "details": details,
        })
        if len(skill.evolution_log) > 20:
            skill.evolution_log = skill.evolution_log[-20:]

    async def get_skill(self, skill_id: str) -> Optional[Skill]:
        await self._ensure_loaded()
        return self._skills.get(skill_id)

    async def list_skills(self, category: Optional[str] = None) -> list[Skill]:
        await self._ensure_loaded()
        skills = list(self._skills.values())
        if category:
            skills = [s for s in skills if s.category == category]
        return sorted(skills, key=lambda s: s.use_count, reverse=True)

    # ── Tier 1 / Tier 2 访问 ──

    def get_skill_metadata(self, skill_id: str) -> Optional[dict[str, Any]]:
        """Tier 1: 返回元数据 (轻量)."""
        skill = self._skills.get(skill_id)
        return skill.to_metadata() if skill else None

    def get_skill_content(self, skill_id: str) -> Optional[str]:
        """L2: 返回完整 Markdown 内容."""
        skill = self._skills.get(skill_id)
        return skill.to_full_content() if skill else None

    def get_skill_resources(self, skill_id: str) -> dict[str, str]:
        """L3: 返回技能附属资源文件 (references/ + assets/).

        遵循 AgentSkills 标准的渐进式披露:
        L1 = name + description (system prompt)
        L2 = SKILL.md body (user message)
        L3 = references/ + assets/ (按需读取)
        """
        skill_dir = self._skills_dir / skill_id
        resources: dict[str, str] = {}
        for subdir in ("references", "assets", "scripts"):
            d = skill_dir / subdir
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.is_file() and f.stat().st_size < 50_000:  # 50KB 限制
                    try:
                        resources[f"{subdir}/{f.name}"] = f.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        resources[f"{subdir}/{f.name}"] = f"(binary file, {f.stat().st_size} bytes)"
        return resources

    # ── 匹配 ──

    async def match_skill(
        self,
        user_message: str,
        model_router: Optional[Any] = None,
    ) -> Optional[Skill]:
        """匹配最合适的技能 (关键词 → LLM 语义)."""
        await self._ensure_loaded()
        if not self._skills:
            return None

        lower_msg = user_message.lower()
        best_match: Optional[Skill] = None
        best_score = 0

        for skill in self._skills.values():
            score = 0
            trigger_words = skill.trigger.lower().split()
            for word in trigger_words:
                min_len = 1 if any('\u4e00' <= c <= '\u9fff' for c in word) else 3
                if len(word) > min_len and word in lower_msg:
                    score += 1
            for tag in skill.tags:
                if tag.lower() in lower_msg:
                    score += 2
            for example in skill.examples:
                if any(w in lower_msg for w in example.lower().split()
                       if len(w) > (1 if any('\u4e00' <= c <= '\u9fff' for c in w) else 2)):
                    score += 1
            if score > best_score:
                best_score = score
                best_match = skill

        if best_score >= 2:
            logger.info("Keyword matched skill: %s (score=%d)",
                        best_match.name if best_match else "?", best_score)
            return best_match

        # LLM 语义匹配
        if model_router and self._skills:
            try:
                skills_desc = "\n".join(
                    f"- ID: {s.skill_id}, Name: {s.name}, Trigger: {s.trigger}"
                    for s in self._skills.values()
                )
                prompt = SKILL_MATCHING_PROMPT.format(
                    user_message=user_message, skills_list=skills_desc,
                )
                from agent.providers.base import Message
                response = await model_router.complete_with_failover(
                    messages=[Message(role="user", content=prompt)],
                    user_message=prompt, temperature=0.1,
                )
                content = response.content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1] if "\n" in content else content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                result = json.loads(content.strip())
                matched_id = result.get("matched_skill_id")
                confidence = result.get("confidence", 0)
                if matched_id and confidence >= 0.85:
                    skill = self._skills.get(matched_id)
                    if skill:
                        logger.info("LLM matched skill: %s (confidence=%.2f)", skill.name, confidence)
                        return skill
            except (ValueError, json.JSONDecodeError, KeyError) as e:
                logger.debug("LLM skill matching failed: %s", e)

        return None

    # ── 使用记录 ──

    async def record_usage(self, skill_id: str, success: bool = True) -> None:
        skill = self._skills.get(skill_id)
        if skill:
            skill.use_count += 1
            if not success:
                skill.failure_count += 1
            alpha = 0.3
            skill.success_rate = alpha * (1.0 if success else 0.0) + (1 - alpha) * skill.success_rate
            skill.updated_at = time.time()
            await self._save_skill(skill)

    async def record_failure(self, skill_id: str, error_info: str = "") -> None:
        await self.record_usage(skill_id, success=False)

    # ── 自动提取 ──

    async def extract_from_conversation(
        self,
        messages: list[dict[str, Any]],
        model_router: Optional[Any] = None,
    ) -> Optional[Skill]:
        """从成功的对话中自动提取技能 (输出 SKILL.md 格式)."""
        if not model_router or not messages:
            return None

        has_tool_calls = any(
            m.get("tool_calls") or m.get("role") == "tool" for m in messages
        )
        if not has_tool_calls:
            return None

        conversation = "\n".join(
            f"{m.get('role', '?')}: {str(m.get('content', ''))[:200]}"
            for m in messages[-20:]
        )

        try:
            from agent.providers.base import Message
            prompt = SKILL_EXTRACTION_PROMPT.format(conversation=conversation)
            response = await model_router.complete_with_failover(
                messages=[Message(role="user", content=prompt)],
                user_message=prompt, temperature=0.3,
            )

            content = response.content.strip()
            if content == "null" or not content:
                return None

            # 提取 markdown 代码块
            md_match = re.search(r"```markdown\s*\n(.*?)```", content, re.DOTALL)
            if md_match:
                content = md_match.group(1).strip()
            elif content.startswith("---"):
                pass  # 直接是 SKILL.md 格式
            else:
                return None

            skill = Skill.from_skill_md(content)
            if not skill.name:
                return None

            # 检查重复
            for existing in self._skills.values():
                if existing.name.lower() == skill.name.lower():
                    logger.debug("Skill '%s' already exists, skipping", skill.name)
                    return None

            # 创建
            created = await self.create_skill(
                name=skill.name,
                description=skill.description,
                trigger=skill.trigger,
                body=skill.body,
                category=skill.category,
                tags=skill.tags,
                examples=skill.examples,
            )
            logger.info("Auto-extracted skill: %s", created.name)
            return created

        except (ValueError, json.JSONDecodeError, OSError) as e:
            logger.warning("Skill extraction failed: %s", e)
            return None

    # ── Prompt 生成 ──

    def get_skills_prompt(self, limit: int = 10, max_inject_tokens: int = 2000) -> str:
        """生成技能概览 (L1 tier) — 用于 system prompt.

        遵循 AgentSkills 标准: 只注入 name + description + 路径，
        最小化 token 消耗。超出 token 预算的技能只注入 name。

        Args:
            limit: 最多注入的技能数量
            max_inject_tokens: token 预算 (按 1 token ≈ 3 chars 估算)
        """
        if not self._skills:
            return ""

        top_skills = sorted(
            self._skills.values(),
            key=lambda s: (s.use_count, s.success_rate),
            reverse=True,
        )[:limit]

        header = "\n## 可用技能 (L1)\n遇到匹配的任务时，读取对应 SKILL.md 获取完整指令:\n\n"
        max_chars = max_inject_tokens * 3
        used_chars = len(header)
        lines = []

        for s in top_skills:
            path = self._skills_dir / s.skill_id / "SKILL.md"
            full_line = f"- {s.name}: {s.description} ({path})"
            short_line = f"- {s.name} ({path})"
            if used_chars + len(full_line) <= max_chars:
                lines.append(full_line)
                used_chars += len(full_line) + 1
            elif used_chars + len(short_line) <= max_chars:
                lines.append(short_line)
                used_chars += len(short_line) + 1
            else:
                break

        if not lines:
            return ""
        return header + "\n".join(lines) + "\n"

    # ── 持久化 ──

    async def _save_skill(self, skill: Skill) -> None:
        """保存技能为 SKILL.md 格式 (获取锁)."""
        async with self._lock:
            await self._save_skill_unlocked(skill)

    async def _save_skill_unlocked(self, skill: Skill) -> None:
        """保存技能为 SKILL.md 格式 (调用方需持有锁)."""
        _validate_skill_id(skill.skill_id)
        skill_dir = self._skills_dir / skill.skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        md_path = skill_dir / "SKILL.md"
        md_path.write_text(skill.to_skill_md(), encoding="utf-8")
