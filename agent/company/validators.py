"""阶段输出验证器 — 确保 LLM 输出满足阶段要求后才推进流水线."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    rework_hint: str = ""


def validate_prd(content: str) -> ValidationResult:
    """PRD 必须是完整文档，不能只是提问."""
    if len(content) < 500:
        clarify_signals = [
            "需要确认", "需要澄清", "请问", "请确认",
            "哪个方向", "几个可能性", "NEED_CLARIFY",
        ]
        if any(s in content for s in clarify_signals):
            return ValidationResult(
                valid=False,
                reason=f"输出为提问而非文档（{len(content)}字）",
                rework_hint=(
                    "你的输出是提问而非 PRD 文档。请直接根据需求编写完整的产品需求文档"
                    "（Markdown 格式），包含项目概述、功能需求、技术约束等章节。"
                    "如果需求不清晰，在文档末尾的「待确认事项」中列出问题。"
                ),
            )
    return ValidationResult(valid=True)


def validate_html_output(content: str, project_dir=None) -> ValidationResult:
    """原型图/UI 设计必须包含有效 HTML（文本中或磁盘文件）."""
    has_html = "<html" in content.lower() or "```html" in content
    if not has_html and project_dir:
        from pathlib import Path
        pdir = Path(project_dir) if not isinstance(project_dir, Path) else project_dir
        for subdir in ("prototypes", "ui-designs"):
            d = pdir / subdir
            if d.exists():
                html_files = list(d.glob("*.html"))
                if html_files:
                    has_html = True
                    break
    if not has_html:
        return ValidationResult(
            valid=False,
            reason="输出不包含有效 HTML",
            rework_hint=(
                "你的输出不包含 HTML 文件。请严格按照要求，"
                "为每个页面生成完整的 HTML 文件（包含 <html><head><body>），"
                "不要只输出文字说明。必须使用 write_file 工具将 HTML 写入文件。"
            ),
        )
    return ValidationResult(valid=True)


def validate_design(content: str) -> ValidationResult:
    """技术设计必须有结构化章节."""
    headings = re.findall(r'^#{1,3}\s+.+', content, re.MULTILINE)
    if len(headings) < 3:
        return ValidationResult(
            valid=False,
            reason=f"设计文档章节不足（仅 {len(headings)} 个标题）",
            rework_hint=(
                "你的输出缺少结构化章节。请输出完整的技术设计文档，"
                "至少包含：技术选型、架构概览、数据模型、文件清单等章节。"
            ),
        )
    return ValidationResult(valid=True)


def validate_code_output(content: str) -> ValidationResult:
    """代码阶段必须实际写了文件."""
    wrote_file = ("write_file" in content or "已创建" in content
                  or "已写入" in content or "```" in content)
    if not wrote_file:
        return ValidationResult(
            valid=False,
            reason="未检测到代码文件输出",
            rework_hint="请使用 write_file 工具编写代码文件，不要只输出文字说明。",
        )
    return ValidationResult(valid=True)


STAGE_VALIDATORS: dict[str, Callable] = {
    "WritePRD": validate_prd,
    "WritePrototype": validate_html_output,
    "WriteUIDesign": validate_html_output,
    "WriteDesign": validate_design,
    "WriteCode": validate_code_output,
}


def validate_checkpoint_output(stage: str, output: str, project_dir=None) -> bool:
    """断点恢复时验证已保存的阶段输出是否有效."""
    if stage == "PRD":
        return len(output) >= 500
    if stage in ("Prototype", "UIDesign"):
        has_html = "<html" in output.lower() or "```html" in output
        if has_html:
            return True
        if project_dir:
            subdir = "prototypes" if stage == "Prototype" else "ui-designs"
            return (project_dir / subdir).exists()
        return False
    if stage == "Design":
        headings = re.findall(r'^#{1,3}\s+.+', output, re.MULTILINE)
        return len(headings) >= 3
    return True
