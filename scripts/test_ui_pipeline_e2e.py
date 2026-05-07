"""端到端测试脚本 — 验证 UI 设计 pipeline 真实输出质量.

用法:
    python3 scripts/test_ui_pipeline_e2e.py --platform web
    python3 scripts/test_ui_pipeline_e2e.py --platform mobile
    python3 scripts/test_ui_pipeline_e2e.py --platform miniprogram

需要: 配置好 LLM provider（OPENAI_API_KEY 或 ANTHROPIC_API_KEY）
"""

import asyncio
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.company.action import Action
from agent.company.validators import validate_html_quality, _validate_ui_design
from unittest.mock import MagicMock


TEST_REQUIREMENTS = {
    "web": "设计一个 AI 写作助手的官网 landing page，包含 Hero、功能介绍、定价、CTA",
    "mobile": "设计一个健身打卡 App 的主要页面：首页（今日计划）、运动详情页、个人中心",
    "miniprogram": "设计一个社区团购小程序：首页（Banner+商品列表）、商品详情、我的订单",
}


async def test_template_injection(platform: str):
    """测试模板注入是否正确."""
    print(f"\n{'='*60}")
    print(f"[1/3] 测试模板注入 — platform={platform}")
    print(f"{'='*60}")

    action = Action.__new__(Action)
    role = MagicMock()
    role._ui_platform = platform
    role._ui_design_system = "wechat" if platform == "miniprogram" else "default"

    prompt = "Design System:\n{design_system}\n\nSeed:\n{seed_template}\n\nLayouts:\n{layouts}\n\nChecklist:\n{checklist}"
    result = action._inject_ui_templates(prompt, role)

    assert "{seed_template}" not in result, "seed_template 未替换"
    assert "{layouts}" not in result, "layouts 未替换"
    assert "{checklist}" not in result, "checklist 未替换"
    assert "{design_system}" not in result, "design_system 未替换"

    print(f"  注入成功: {len(result)} 字符")
    print(f"  包含 :root 变量: {'--accent' in result}")
    print(f"  包含布局模板: {'原型 A' in result}")
    print(f"  包含检查清单: {'P0' in result}")
    return result


async def test_llm_generation(platform: str, injected_prompt: str):
    """用真实 LLM 生成 UI 设计，验证输出质量."""
    print(f"\n{'='*60}")
    print(f"[2/3] LLM 生成测试 — platform={platform}")
    print(f"{'='*60}")

    requirement = TEST_REQUIREMENTS[platform]
    print(f"  需求: {requirement}")

    try:
        from agent.core.engine import AgentEngine
        engine = AgentEngine()
    except Exception as e:
        print(f"  [跳过] 无法初始化 LLM engine: {e}")
        print(f"  请确保配置了 OPENAI_API_KEY 或 ANTHROPIC_API_KEY")
        return None

    # 构建完整 prompt
    full_prompt = f"""你是一个专业 UI 设计师。请根据以下需求生成 UI 设计稿。

## 需求
{requirement}

## 设计系统
{injected_prompt.split('Seed:')[0].split('Design System:')[1].strip()[:2000]}

## 规则
1. 复制 seed template 作为基础
2. 从 layouts 中选择合适的布局粘贴
3. 替换所有 [REPLACE] 为真实内容
4. 不要自己写 CSS，只用 seed 中的 class
5. 不要用 emoji 图标
6. accent 颜色每页最多用 2 次

请直接输出完整的 HTML 文件（用 ```html 包裹）。只输出一个页面。"""

    print(f"  正在调用 LLM...")
    try:
        response = await engine.chat(
            messages=[{"role": "user", "content": full_prompt}],
            model=None,  # 使用默认模型
        )
        content = response.get("content", "") if isinstance(response, dict) else str(response)
        print(f"  LLM 输出: {len(content)} 字符")
        return content
    except Exception as e:
        print(f"  [失败] LLM 调用出错: {e}")
        return None


async def test_validation(content: str, platform: str):
    """验证 LLM 输出是否通过质量检查."""
    print(f"\n{'='*60}")
    print(f"[3/3] 质量验证 — platform={platform}")
    print(f"{'='*60}")

    if not content:
        print("  [跳过] 无 LLM 输出")
        return

    result = _validate_ui_design(content)

    if result.valid:
        print(f"  ✓ 验证通过！LLM 输出符合模板规范")
    else:
        print(f"  ✗ 验证失败: {result.reason}")
        print(f"  修复提示: {result.rework_hint}")

    # 额外信息
    import re
    html_blocks = re.findall(r'```html\s*\n(.*?)```', content, re.DOTALL)
    if html_blocks:
        html = html_blocks[0]
        print(f"\n  --- 输出分析 ---")
        print(f"  HTML 长度: {len(html)} 字符")
        print(f"  包含 [REPLACE]: {'[REPLACE]' in html}")
        print(f"  包含 <style>: {'<style' in html.split('</head>')[1] if '</head>' in html else 'N/A'}")

        hex_in_body = re.findall(r'#[0-9a-fA-F]{3,8}', html.split('</style>')[-1] if '</style>' in html else '')
        print(f"  body 中 hex 色值: {hex_in_body[:5] if hex_in_body else '无'}")

        classes_used = set()
        for cls_attr in re.findall(r'class="([^"]*)"', html):
            classes_used.update(cls_attr.split())
        print(f"  使用的 class 数: {len(classes_used)}")
        print(f"  示例 class: {', '.join(sorted(classes_used)[:10])}")

    return result


async def main():
    parser = argparse.ArgumentParser(description="UI 设计 pipeline 端到端测试")
    parser.add_argument("--platform", choices=["web", "mobile", "miniprogram"], default="web")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 调用，只测模板注入和验证")
    args = parser.parse_args()

    print(f"UI 设计 Pipeline 端到端测试")
    print(f"平台: {args.platform}")

    # Step 1: 模板注入
    injected = await test_template_injection(args.platform)

    # Step 2: LLM 生成
    if args.skip_llm:
        print(f"\n[2/3] [跳过] --skip-llm 模式")
        content = None
    else:
        content = await test_llm_generation(args.platform, injected)

    # Step 3: 验证
    if content:
        await test_validation(content, args.platform)
    else:
        print(f"\n[3/3] [跳过] 无 LLM 输出可验证")
        print(f"\n提示: 如果要测试验证逻辑，可以手动准备一个 HTML 文件:")
        print(f"  python3 -c \"from agent.company.validators import validate_html_quality; ...")

    print(f"\n{'='*60}")
    print(f"测试完成")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
