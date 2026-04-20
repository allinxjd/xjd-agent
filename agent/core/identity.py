"""Agent 身份模板 — 可定制的 Agent 人格与行为.

用法:
    identity = AgentIdentity.load("assistant")
    system_prompt = identity.to_system_prompt()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# 默认身份目录
_DEFAULT_IDENTITIES_DIR = Path(__file__).parent.parent.parent / "identities"

@dataclass
class AgentIdentity:
    """Agent 身份定义."""

    name: str = "XJD Agent"
    role: str = "AI 助手"
    personality: str = "专业、友好、高效"
    language: str = "zh-CN"
    tone: str = "professional"
    rules: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    restrictions: list[str] = field(default_factory=list)
    greeting: str = "你好！我是小巨蛋智能体，有什么可以帮你的？"
    custom_instructions: str = ""

    def to_system_prompt(self) -> str:
        """生成 system prompt."""
        parts = [
            f"你是 {self.name}，一个{self.role}。",
            f"性格特点: {self.personality}",
        ]

        if self.capabilities:
            parts.append("你的能力:")
            for cap in self.capabilities:
                parts.append(f"- {cap}")

        if self.rules:
            parts.append("行为规则:")
            for rule in self.rules:
                parts.append(f"- {rule}")

        if self.restrictions:
            parts.append("限制:")
            for r in self.restrictions:
                parts.append(f"- {r}")

        if self.custom_instructions:
            parts.append(self.custom_instructions)

        return "\n".join(parts)

    @classmethod
    def load(cls, name: str, identities_dir: Optional[Path] = None) -> "AgentIdentity":
        """从 YAML 文件加载身份模板."""
        search_dir = identities_dir or _DEFAULT_IDENTITIES_DIR
        file_path = search_dir / f"{name}.yaml"

        if not file_path.exists():
            file_path = search_dir / f"{name}.yml"

        if not file_path.exists():
            logger.warning("身份模板 %s 不存在，使用默认", name)
            return cls()

        try:
            data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except Exception as e:
            logger.error("加载身份模板失败: %s", e)
            return cls()

    def save(self, path: Path) -> None:
        """保存身份模板到 YAML."""
        from dataclasses import asdict

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.dump(asdict(self), allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    @classmethod
    def list_available(cls, identities_dir: Optional[Path] = None) -> list[str]:
        """列出所有可用的身份模板."""
        search_dir = identities_dir or _DEFAULT_IDENTITIES_DIR
        if not search_dir.exists():
            return []
        return [
            f.stem
            for f in search_dir.iterdir()
            if f.suffix in (".yaml", ".yml")
        ]

# 预置身份模板
BUILTIN_IDENTITIES = {
    "assistant": AgentIdentity(
        name="XJD Agent",
        role="通用 AI 助手",
        personality="专业、友好、高效",
        capabilities=["代码编写与调试", "文件操作", "Web 搜索", "数据分析", "文档生成"],
        rules=["回答准确简洁", "不确定时主动说明", "保护用户隐私"],
    ),
    "coder": AgentIdentity(
        name="XJD Coder",
        role="编程助手",
        personality="严谨、注重代码质量",
        tone="technical",
        capabilities=["代码编写", "代码审查", "调试", "重构", "测试编写", "架构设计"],
        rules=["遵循最佳实践", "写清晰的注释", "考虑边界情况", "优先安全性"],
    ),
    "researcher": AgentIdentity(
        name="XJD Researcher",
        role="研究助手",
        personality="好奇、严谨、善于总结",
        capabilities=["Web 搜索", "文献分析", "数据整理", "报告生成"],
        rules=["引用来源", "区分事实与观点", "多角度分析"],
    ),
    "ops": AgentIdentity(
        name="XJD Ops",
        role="运维助手",
        personality="谨慎、注重安全",
        capabilities=["服务器管理", "日志分析", "监控告警", "部署自动化"],
        rules=["操作前确认", "保留回滚方案", "记录变更日志"],
        restrictions=["不执行破坏性操作除非明确确认", "不暴露敏感信息"],
    ),
}
