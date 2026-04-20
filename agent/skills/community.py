"""技能社区 — Markdown 导入/导出 + 技能分享.

核心能力:
1. Markdown 导出 — 技能转为人类可读的 Markdown
2. Markdown 导入 — 从 Markdown 文件导入技能
3. 批量导入/导出 — 技能包管理
4. 技能索引 — 生成可浏览的技能目录
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

class SkillCommunity:
    """技能社区 — 导入/导出/分享."""

    def __init__(self, skill_manager: Any) -> None:
        self._skill_manager = skill_manager

    def skill_to_markdown(self, skill: Any) -> str:
        """将技能导出为 Markdown 格式."""
        lines = [
            f"# {skill.name}",
            "",
            f"> {skill.description}",
            "",
            f"**Category:** {skill.category}  ",
            f"**Version:** {skill.version}  ",
            f"**Success Rate:** {skill.success_rate * 100:.0f}%  ",
            f"**Uses:** {skill.use_count}",
            "",
        ]

        if skill.tags:
            lines.append(f"**Tags:** {', '.join(skill.tags)}")
            lines.append("")

        # Trigger
        lines.extend(["## Trigger", "", skill.trigger, ""])

        # Examples
        if skill.examples:
            lines.append("## Examples")
            lines.append("")
            for ex in skill.examples:
                lines.append(f"- {ex}")
            lines.append("")

        # Steps
        lines.append("## Steps")
        lines.append("")
        for i, step in enumerate(skill.steps, 1):
            desc = step.get("description", "")
            tool = step.get("tool", "")
            if tool:
                lines.append(f"{i}. **{desc}**")
                lines.append(f"   - Tool: `{tool}`")
                args = step.get("args_template", step.get("args", {}))
                if args:
                    lines.append(f"   - Args: `{args}`")
            else:
                lines.append(f"{i}. {desc}")
        lines.append("")

        # Metadata
        lines.extend([
            "---",
            f"skill_id: {skill.skill_id}  ",
            f"created: {time.strftime('%Y-%m-%d', time.localtime(skill.created_at))}  ",
            f"updated: {time.strftime('%Y-%m-%d', time.localtime(skill.updated_at))}",
        ])

        return "\n".join(lines)

    def markdown_to_skill_data(self, content: str) -> Optional[dict[str, Any]]:
        """从 Markdown 解析技能数据."""
        lines = content.strip().split("\n")
        if not lines:
            return None

        data: dict[str, Any] = {
            "skill_id": str(uuid.uuid4())[:8],
            "steps": [],
            "tags": [],
            "examples": [],
            "category": "general",
            "created_at": time.time(),
            "updated_at": time.time(),
        }

        # Parse name from H1
        if lines[0].startswith("# "):
            data["name"] = lines[0][2:].strip()

        current_section = ""
        for line in lines[1:]:
            stripped = line.strip()

            # Section headers
            if stripped.startswith("## "):
                current_section = stripped[3:].strip().lower()
                continue

            # Description from blockquote
            if stripped.startswith("> ") and "description" not in data:
                data["description"] = stripped[2:].strip()
                continue

            # Metadata fields
            if stripped.startswith("**Category:**"):
                data["category"] = stripped.split(":", 1)[1].strip().rstrip("  ").strip("* ")
            elif stripped.startswith("**Tags:**"):
                tags_str = stripped.split(":", 1)[1].strip()
                data["tags"] = [t.strip() for t in tags_str.split(",") if t.strip()]

            # Trigger section
            if current_section == "trigger" and stripped and not stripped.startswith("**"):
                data["trigger"] = stripped

            # Examples section
            if current_section == "examples" and stripped.startswith("- "):
                data["examples"].append(stripped[2:])

            # Steps section
            if current_section == "steps":
                step_match = re.match(r"^\d+\.\s+\*?\*?(.+?)\*?\*?$", stripped)
                if step_match:
                    data["steps"].append({"description": step_match.group(1).strip("* ")})
                elif stripped.startswith("- Tool:") and data["steps"]:
                    tool = stripped.split("`")[1] if "`" in stripped else stripped.split(":", 1)[1].strip()
                    data["steps"][-1]["tool"] = tool

            # Footer metadata
            if stripped.startswith("skill_id:"):
                data["skill_id"] = stripped.split(":", 1)[1].strip()

        if not data.get("name"):
            return None

        return data

    async def export_skill(self, skill_id: str, output_path: str) -> bool:
        """导出单个技能为 Markdown 文件."""
        skill = await self._skill_manager.get_skill(skill_id)
        if not skill:
            return False

        md = self.skill_to_markdown(skill)
        Path(output_path).write_text(md, encoding="utf-8")
        logger.info("Exported skill %s to %s", skill.name, output_path)
        return True

    async def import_skill(self, input_path: str) -> Optional[str]:
        """从 Markdown 文件导入技能."""
        path = Path(input_path)
        if not path.exists():
            return None

        content = path.read_text(encoding="utf-8")
        data = self.markdown_to_skill_data(content)
        if not data:
            return None

        skill = await self._skill_manager.create_skill(
            name=data["name"],
            description=data.get("description", ""),
            trigger=data.get("trigger", ""),
            steps=data.get("steps", []),
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            examples=data.get("examples", []),
        )
        logger.info("Imported skill %s from %s", skill.name, input_path)
        return skill.skill_id

    async def export_all(self, output_dir: str) -> int:
        """批量导出所有技能."""
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        skills = await self._skill_manager.list_skills()
        count = 0
        for skill in skills:
            if "deprecated" in skill.tags:
                continue
            safe_name = re.sub(r"[^\w\-]", "_", skill.name)
            path = out / f"{safe_name}.md"
            md = self.skill_to_markdown(skill)
            path.write_text(md, encoding="utf-8")
            count += 1

        # Generate index
        index_lines = ["# Skill Index", "", f"Total: {count} skills", ""]
        for skill in skills:
            if "deprecated" not in skill.tags:
                safe_name = re.sub(r"[^\w\-]", "_", skill.name)
                index_lines.append(
                    f"- [{skill.name}]({safe_name}.md) — {skill.description}"
                )
        (out / "INDEX.md").write_text("\n".join(index_lines), encoding="utf-8")

        logger.info("Exported %d skills to %s", count, output_dir)
        return count

    async def import_all(self, input_dir: str) -> int:
        """批量导入目录下所有 Markdown 技能."""
        inp = Path(input_dir)
        if not inp.is_dir():
            return 0

        count = 0
        for path in inp.glob("*.md"):
            if path.name == "INDEX.md":
                continue
            skill_id = await self.import_skill(str(path))
            if skill_id:
                count += 1

        logger.info("Imported %d skills from %s", count, input_dir)
        return count
