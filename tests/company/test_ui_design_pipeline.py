"""UI 设计管线端到端测试 — 验证质量规则、图片增强、导出管道."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from agent.company.validators import (
    ValidationResult,
    validate_html_quality,
    _validate_ui_design,
    _check_hex_outside_root,
    _check_custom_style,
    _check_class_whitelist,
    _check_accent_overuse,
    _check_replace_placeholders,
    _check_emoji,
    _check_data_section,
    _detect_platform_from_html,
)


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

GOOD_WEB_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Test</title>
<style>
:root { --bg:#FAFAFA; --surface:#FFFFFF; --fg:#111; --muted:#6B6B6B; --border:#E5E5E5; --accent:#2F6FEB; }
</style>
</head>
<body>
<section class="section hero" data-section="hero">
  <div class="container hero-center">
    <p class="eyebrow">SaaS Platform</p>
    <h1 class="display">Build faster with AI</h1>
    <p class="lead">Ship products in days, not months.</p>
    <div class="hero-cta">
      <a class="btn btn-primary" href="#">Start Free</a>
      <a class="btn btn-secondary" href="#">Learn More</a>
    </div>
  </div>
</section>
</body></html>"""

GOOD_MOBILE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>App</title>
<style>
:root { --bg:#EDEDED; --surface:#FFF; --fg:#000; --muted:#888; --border:#E5E5E5; --accent:#07C160; }
</style>
</head>
<body>
<div class="device">
  <div class="screen">
    <div class="navbar"><span class="back"></span><span class="title">Profile</span></div>
    <div class="content pad">
      <div class="card"><p class="meta">User info</p></div>
    </div>
  </div>
</div>
</body></html>"""


# ═══════════════════════════════════════════════════════════════════
#  TestHtmlQualityValidator
# ═══════════════════════════════════════════════════════════════════

class TestHtmlQualityValidator:
    """单元测试：每条 P0 规则的正反例."""

    def test_hex_outside_root_detected(self):
        html = '<html><head><style>:root{--bg:#fff;}</style></head><body><div style="color:#FF0000">x</div></body></html>'
        err = _check_hex_outside_root(html)
        assert err is not None
        assert "#FF0000" in err

    def test_hex_inside_root_passes(self):
        html = '<html><head><style>:root{--bg:#FAFAFA; --accent:#2F6FEB;}</style></head><body><div>ok</div></body></html>'
        err = _check_hex_outside_root(html)
        assert err is None

    def test_custom_style_detected(self):
        html = '<html><head><style>:root{}</style></head><body><style>.custom{color:red}</style><div>x</div></body></html>'
        err = _check_custom_style(html)
        assert err is not None

    def test_inline_style_allowed(self):
        html = '<html><head><style>:root{}</style></head><body><div style="margin-top:8px">ok</div></body></html>'
        err = _check_custom_style(html)
        assert err is None

    def test_unknown_class_detected(self):
        html = '<html><head><style>:root{}</style></head><body><div class="aaa bbb ccc ddd eee fff ggg">x</div></body></html>'
        err = _check_class_whitelist(html, "web")
        assert err is not None
        assert "seed 未定义" in err

    def test_seed_class_passes(self):
        html = '<html><head><style>:root{}</style></head><body><div class="container hero-center"><a class="btn btn-primary">ok</a></div></body></html>'
        err = _check_class_whitelist(html, "web")
        assert err is None

    def test_accent_overuse_detected(self):
        html = '<section data-section="hero">var(--accent) var(--accent) var(--accent)</section>'
        err = _check_accent_overuse(html)
        assert err is not None

    def test_accent_within_limit_passes(self):
        html = '<section data-section="a">var(--accent) var(--accent)</section><section data-section="b">var(--accent)</section>'
        err = _check_accent_overuse(html)
        assert err is None

    def test_replace_placeholder_detected(self):
        html = '<h1>[REPLACE] Title</h1>'
        err = _check_replace_placeholders(html)
        assert err is not None

    def test_no_replace_passes(self):
        html = '<h1>Real Title</h1>'
        err = _check_replace_placeholders(html)
        assert err is None

    def test_emoji_detected(self):
        html = '<html><head><style>:root{}</style></head><body><div>🚀 Feature</div></body></html>'
        err = _check_emoji(html)
        assert err is not None

    def test_no_emoji_passes(self):
        html = '<html><head><style>:root{}</style></head><body><div>Feature</div></body></html>'
        err = _check_emoji(html)
        assert err is None

    def test_data_section_missing_web(self):
        html = '<section class="section"><div>content</div></section>'
        err = _check_data_section(html, "web")
        assert err is not None

    def test_data_section_present_passes(self):
        html = '<section class="section" data-section="hero"><div>content</div></section>'
        err = _check_data_section(html, "web")
        assert err is None

    def test_data_section_skipped_for_mobile(self):
        html = '<section class="section"><div>content</div></section>'
        err = _check_data_section(html, "mobile")
        assert err is None

    def test_valid_web_html_passes_all(self):
        result = validate_html_quality(GOOD_WEB_HTML)
        assert result.valid is True

    def test_valid_mobile_html_passes_all(self):
        result = validate_html_quality(GOOD_MOBILE_HTML)
        assert result.valid is True

    def test_platform_detection_mobile(self):
        assert _detect_platform_from_html('<div class="device">') == "mobile"

    def test_platform_detection_web(self):
        assert _detect_platform_from_html('<section class="hero">') == "web"


# ═══════════════════════════════════════════════════════════════════
#  TestValidationIntegration
# ═══════════════════════════════════════════════════════════════════

class TestValidationIntegration:
    """验证器与 stage_handler 的集成."""

    def test_validate_ui_design_rejects_no_html(self):
        result = _validate_ui_design("just some text, no html")
        assert result.valid is False
        assert "HTML" in result.rework_hint

    def test_validate_ui_design_rejects_bad_quality(self):
        bad = '```html\n<html><body><div style="color:#FF0000">[REPLACE]</div></body></html>\n```'
        result = _validate_ui_design(bad)
        assert result.valid is False

    def test_validate_ui_design_passes_good(self):
        wrapped = f"```html\n{GOOD_WEB_HTML}\n```"
        result = _validate_ui_design(wrapped)
        assert result.valid is True


# ═══════════════════════════════════════════════════════════════════
#  TestImageEnhancement
# ═══════════════════════════════════════════════════════════════════

class TestImageEnhancement:
    """占位图替换逻辑测试."""

    @pytest.fixture
    def ui_dir(self, tmp_path):
        """创建临时 UI 设计目录."""
        proj = tmp_path / "project"
        ui = proj / "ui-designs"
        ui.mkdir(parents=True)
        html = ui / "page1.html"
        html.write_text(
            '<html><body><div class="ph-img wide">产品展示图</div></body></html>',
            encoding="utf-8",
        )
        return proj

    @pytest.mark.asyncio
    async def test_dalle_called_for_general_project(self, ui_dir):
        """通用项目调用 DALL-E."""
        from agent.company.company import Company

        company = MagicMock(spec=Company)
        company._env = MagicMock()
        company._env.roles = {"PM": MagicMock(_requirement_text="SaaS landing page", _ui_platform="web")}

        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("agent.tools.media_tools._image_generate", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "图片已生成: https://example.com/img.png"

            with patch("httpx.AsyncClient") as mock_client_cls:
                mock_resp = AsyncMock()
                mock_resp.status_code = 200
                mock_resp.content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
                mock_client_inst = AsyncMock()
                mock_client_inst.get = AsyncMock(return_value=mock_resp)
                mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
                mock_client_inst.__aexit__ = AsyncMock(return_value=False)
                mock_client_cls.return_value = mock_client_inst

                await Company._enhance_placeholder_images(company, ui_dir, MagicMock())
                mock_gen.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_when_no_api_key(self, ui_dir):
        """无 API key 时不崩溃，保留原始 HTML."""
        from agent.company.company import Company

        company = MagicMock(spec=Company)
        company._env = MagicMock()
        company._env.roles = {"PM": MagicMock(_requirement_text="landing page", _ui_platform="web")}

        with patch.dict("os.environ", {}, clear=True):
            # 确保没有 OPENAI_API_KEY
            import os
            os.environ.pop("OPENAI_API_KEY", None)
            await Company._enhance_placeholder_images(company, ui_dir, MagicMock())

        html = (ui_dir / "ui-designs" / "page1.html").read_text()
        assert "ph-img" in html

    @pytest.mark.asyncio
    async def test_ecommerce_called_with_reference(self, ui_dir):
        """电商项目有参考图时调用电商做图."""
        from agent.company.company import Company

        # 创建参考图
        ref_dir = ui_dir / "assets"
        ref_dir.mkdir()
        (ref_dir / "product.jpg").write_bytes(b'\xff\xd8\xff\xe0' + b'\x00' * 50)

        company = MagicMock(spec=Company)
        company._env = MagicMock()
        company._env.roles = {"PM": MagicMock(_requirement_text="淘宝电商详情页设计", _ui_platform="mobile")}

        with patch("agent.tools.ecommerce_tools.generate_ecommerce_image", new_callable=AsyncMock) as mock_ecom, \
             patch("agent.core.secrets.get_secrets_store") as mock_secrets:
            mock_secrets.return_value = MagicMock()
            mock_secrets.return_value.get = lambda skill, key, default=None: "test" if key in ("CALABASH_PHONE", "CALABASH_PASSWORD") else default

            import json
            img_path = str(ui_dir / "ui-designs" / "assets" / "taobao_main_123_1.jpg")
            (ui_dir / "ui-designs" / "assets").mkdir(exist_ok=True)
            Path(img_path).write_bytes(b'\xff\xd8' + b'\x00' * 50)
            mock_ecom.return_value = json.dumps({
                "success": True,
                "images": [{"path": img_path, "size_kb": 50}]
            })

            await Company._enhance_placeholder_images(company, ui_dir, MagicMock())
            mock_ecom.assert_called_once()
            assert "taobao" in mock_ecom.call_args.kwargs.get("platform", "")


# ═══════════════════════════════════════════════════════════════════
#  TestTemplateInjection
# ═══════════════════════════════════════════════════════════════════

class TestTemplateInjection:
    """模板注入到 prompt 的测试."""

    def test_injection_replaces_placeholders(self):
        from agent.company.action import Action
        action = Action.__new__(Action)

        role = MagicMock()
        role._ui_platform = "web"
        role._ui_design_system = "default"

        prompt = "Design: {design_system}\nSeed: {seed_template}\nLayouts: {layouts}\nCheck: {checklist}"
        result = action._inject_ui_templates(prompt, role)

        assert "{seed_template}" not in result
        assert "{layouts}" not in result
        assert "{checklist}" not in result
        assert "{design_system}" not in result
        assert ":root" in result
        assert "Hero" in result or "hero" in result
