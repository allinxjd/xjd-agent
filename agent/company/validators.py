"""阶段输出验证器 — 确保 LLM 输出满足阶段要求后才推进流水线."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path as _Path
from typing import Callable, Optional


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    rework_hint: str = ""


# ═══════════════════════════════════════════════════════════════════
#  HTML 质量验证（P0 checklist 程序化检查）
# ═══════════════════════════════════════════════════════════════════

_SEED_CLASSES: dict[str, set[str]] = {}


def _load_seed_classes(platform: str) -> set[str]:
    """从 seed.html 提取所有 CSS class 名称作为白名单."""
    if platform in _SEED_CLASSES:
        return _SEED_CLASSES[platform]
    seed_path = _Path(__file__).parent / "templates" / "ui" / platform / "seed.html"
    if not seed_path.exists():
        return set()
    content = seed_path.read_text(encoding="utf-8")
    # Match CSS class selectors (start with letter or hyphen, not digits/decimals)
    classes = set(re.findall(r'\.([a-zA-Z][a-zA-Z0-9_-]*)', content))
    _SEED_CLASSES[platform] = classes
    return classes


def _detect_platform_from_html(content: str) -> str:
    """从 HTML 内容判断平台类型."""
    if 'class="device"' in content or ".device" in content:
        if "wx-navbar" in content or "wx-capsule" in content:
            return "miniprogram"
        return "mobile"
    return "web"


def _extract_body_content(content: str) -> str:
    """提取 <body> 之后的内容（排除 :root 和 seed style 块）."""
    body_match = re.search(r'<body[^>]*>', content, re.IGNORECASE)
    if body_match:
        return content[body_match.end():]
    style_end = content.rfind('</style>')
    if style_end != -1:
        return content[style_end + 8:]
    return content


_EMOJI_RE = re.compile(
    r'[\U0001F600-\U0001F64F'
    r'\U0001F300-\U0001F5FF'
    r'\U0001F680-\U0001F6FF'
    r'\U0001F900-\U0001F9FF'
    r'\U0001FA00-\U0001FA6F'
    r'\U00002702-\U000027B0'
    r'\U0000FE00-\U0000FE0F'
    r'\U0001F1E0-\U0001F1FF]'
)


def _check_hex_outside_root(content: str) -> Optional[str]:
    """检查 :root 和 <style> 外是否有 hex 色值."""
    parts = content.split('</style>')
    if len(parts) > 1:
        body_content = parts[-1]
    else:
        body_content = _extract_body_content(content)
    # Remove HTML entities (&#10003; etc) and anchor links (#section) before checking
    cleaned = re.sub(r'&#\d+;', '', body_content)
    cleaned = re.sub(r'href="#[^"]*"', '', cleaned)
    matches = re.findall(r'#[0-9a-fA-F]{3,8}\b', cleaned)
    if matches:
        samples = ', '.join(matches[:3])
        return f"发现 :root 外的 hex 色值: {samples}。请改用 CSS 变量（var(--accent) 等）"
    return None


def _check_custom_style(content: str) -> Optional[str]:
    """检查 body 内是否有额外 <style> 块."""
    body_content = _extract_body_content(content)
    if '<style' in body_content.lower():
        return "不允许在 body 内添加 <style> 块，请只使用 seed 中预定义的 class"
    return None


def _check_class_whitelist(content: str, platform: str) -> Optional[str]:
    """检查是否使用了 seed 未定义的 class（允许少量语义化 class）."""
    whitelist = _load_seed_classes(platform)
    if not whitelist:
        return None
    body_content = _extract_body_content(content)
    used_classes = set(re.findall(r'class="([^"]*)"', body_content))
    all_used: set[str] = set()
    for class_attr in used_classes:
        all_used.update(class_attr.split())
    unknown = all_used - whitelist - {''}
    # Allow up to 5 unknown classes (LLMs add semantic names like "pricing-card")
    if len(unknown) > 5:
        samples = ', '.join(sorted(unknown)[:5])
        return f"使用了过多 seed 未定义的 class ({len(unknown)}个): {samples}。请尽量只用 seed.html 中已有的 class"
    return None


def _check_accent_overuse(content: str) -> Optional[str]:
    """检查 accent 使用是否超限."""
    sections = re.findall(r'data-section', content)
    section_count = max(len(sections), 1)
    accent_count = content.count('var(--accent)')
    limit = section_count * 2
    if accent_count > limit:
        return f"accent 使用过多（{accent_count}次，{section_count}个 section 限{limit}次）。请减少 accent 使用"
    return None


def _check_replace_placeholders(content: str) -> Optional[str]:
    """检查是否有未替换的 [REPLACE] 占位符."""
    if '[REPLACE]' in content:
        return "仍有未替换的 [REPLACE] 占位符，请用实际内容替换所有 [REPLACE]"
    return None


def _check_emoji(content: str) -> Optional[str]:
    """检查是否使用了 emoji 图标."""
    body_content = _extract_body_content(content)
    matches = _EMOJI_RE.findall(body_content)
    if matches:
        return f"检测到 emoji 图标（{''.join(matches[:3])}），请用 SVG 或 feature-mark class 替代"
    return None


def _check_data_section(content: str, platform: str) -> Optional[str]:
    """Web 平台检查 section 是否有 data-section 属性."""
    if platform != "web":
        return None
    sections = re.findall(r'<section[^>]*>', content, re.IGNORECASE)
    missing = [s for s in sections if 'data-section' not in s]
    if missing:
        return f"有 {len(missing)} 个 <section> 缺少 data-section 属性，请为每个 section 添加"
    return None


def validate_html_quality(content: str, project_dir=None) -> ValidationResult:
    """P0 质量检查 — 逐条验证 HTML 是否符合模板规范."""
    html_blocks = re.findall(r'```html\s*\n(.*?)```', content, re.DOTALL)
    if not html_blocks:
        html_docs = re.findall(
            r'(<!DOCTYPE html>.*?</html>)', content, re.DOTALL | re.IGNORECASE
        )
        html_blocks = html_docs if html_docs else [content]

    for html in html_blocks:
        platform = _detect_platform_from_html(html)
        checks = [
            _check_replace_placeholders(html),
            _check_hex_outside_root(html),
            _check_custom_style(html),
            _check_emoji(html),
            _check_accent_overuse(html),
            _check_data_section(html, platform),
            _check_class_whitelist(html, platform),
        ]
        for error in checks:
            if error:
                return ValidationResult(valid=False, reason=error, rework_hint=error)
    return ValidationResult(valid=True)


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


def _validate_ui_design(content: str, project_dir=None) -> ValidationResult:
    """WriteUIDesign 专用验证：先检查 HTML 存在，再检查质量."""
    basic = validate_html_output(content, project_dir)
    if not basic.valid:
        return basic
    return validate_html_quality(content, project_dir)


STAGE_VALIDATORS: dict[str, Callable] = {
    "WritePRD": validate_prd,
    "WritePrototype": validate_html_output,
    "WriteUIDesign": _validate_ui_design,
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
