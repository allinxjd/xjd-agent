"""Action — 角色可执行的原子操作.

每个 Action 内部创建 AgentEngine 执行任务（复用 multi_agent.py 的 spawn_agent 模式）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.company.role import CompanyRole

logger = logging.getLogger(__name__)


def _has_workspace_guard(prompt: str) -> bool:
    """检查 prompt 是否包含项目工作目录声明（即有 Workspace Guard 保护）."""
    return "## 项目工作目录\n" in prompt


def _apply_workspace_guard(engine: Any, prompt: str) -> None:
    """如果 prompt 包含项目工作目录，包装 write_file/edit_file/run_terminal 拒绝目录外操作."""
    import re
    from pathlib import Path

    match = re.search(r"## 项目工作目录\n(.+)\n", prompt)
    if not match:
        return

    workspace = Path(match.group(1)).resolve()

    for tool_name in ("write_file", "edit_file"):
        tool_handler = engine._tools.get(tool_name)
        if not tool_handler:
            continue
        original_fn = tool_handler.handler

        def _guarded(orig=original_fn, ws=workspace):
            async def wrapper(**kwargs):
                file_path = kwargs.get("file_path") or kwargs.get("path") or ""
                if file_path:
                    resolved = Path(file_path).resolve()
                    if not str(resolved).startswith(str(ws)):
                        return f"错误：禁止在项目目录外写入文件。目标路径 {file_path} 不在 {ws} 下。请使用项目目录内的路径。"
                return await orig(**kwargs)
            return wrapper

        tool_handler.handler = _guarded()

    terminal_tool = engine._tools.get("run_terminal")
    if terminal_tool:
        original_terminal = terminal_tool.handler

        async def _guarded_terminal(*, _orig=original_terminal, _ws=workspace, **kwargs):
            kwargs["workdir"] = str(_ws)
            command = kwargs.get("command", "")
            if command:
                import shlex
                ws_str = str(_ws)
                dangerous = False
                for token in command.split("&&"):
                    token = token.strip()
                    if token.startswith("cd "):
                        target = token[3:].strip().strip("'\"")
                        if target and not target.startswith(ws_str) and target not in (".", ".."):
                            resolved = Path(target).resolve() if target.startswith("/") else None
                            if resolved and not str(resolved).startswith(ws_str):
                                dangerous = True
                                break
                if dangerous:
                    return f"错误：禁止 cd 到项目目录外。请在 {_ws} 内操作。"
            return await _orig(**kwargs)

        terminal_tool.handler = _guarded_terminal

    for tool_name in ("read_file", "grep_search", "list_directory"):
        tool_handler = engine._tools.get(tool_name)
        if not tool_handler:
            continue
        original_fn = tool_handler.handler

        def _read_guarded(orig=original_fn, ws=workspace):
            async def wrapper(**kwargs):
                for key in ("path", "file_path"):
                    if key in kwargs and kwargs[key]:
                        if not Path(kwargs[key]).is_absolute():
                            kwargs[key] = str(ws / kwargs[key])
                        break
                return await orig(**kwargs)
            return wrapper

        tool_handler.handler = _read_guarded()


@dataclass
class Action:
    """角色可执行的原子操作."""

    name: str
    description: str = ""
    prompt_template: str = ""
    tools_filter: Optional[list[str]] = None
    max_tool_rounds: Optional[int] = None
    use_cheap_model: bool = False

    def _inject_ui_templates(self, prompt: str, role) -> str:
        """注入 UI 设计模板系统文件到 prompt."""
        from pathlib import Path as _P
        templates_dir = _P(__file__).parent / "templates" / "ui"

        platform = getattr(role, '_ui_platform', 'mobile')
        design_system_name = getattr(role, '_ui_design_system', 'default')

        seed_path = templates_dir / platform / "seed.html"
        layouts_path = templates_dir / platform / "layouts.md"
        checklist_path = templates_dir / platform / "checklist.md"
        ds_path = templates_dir / "design-systems" / f"{design_system_name}.md"

        def _read(p: _P) -> str:
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                return f"[文件未找到: {p.name}]"

        prompt = prompt.replace("{seed_template_path}", str(seed_path))
        prompt = prompt.replace("{layouts_path}", str(layouts_path))
        prompt = prompt.replace("{checklist_path}", str(checklist_path))
        prompt = prompt.replace("{design_system}", _read(ds_path))
        return prompt

    async def run(self, context: str, role: CompanyRole) -> str:
        from agent.core.engine import AgentEngine

        if self.name == "WriteUIDesign":
            logger.info("[Action:WriteUIDesign] context长度=%d, 前200字=%s", len(context), context[:200])
        prompt = self.prompt_template.replace("{context}", context) if self.prompt_template else context
        if "{prototype_layout}" in prompt:
            platform = getattr(role, '_ui_platform', 'web')
            if platform == "web":
                prompt = prompt.replace("{prototype_layout}", "桌面 Web 布局（外层 max-w-[1200px] mx-auto，响应式设计）")
            else:
                prompt = prompt.replace("{prototype_layout}", "移动端优先布局（外层 max-w-[375px] mx-auto）")
        boss_title = getattr(role, '_boss_title', '老板')
        if "{boss_title}" in prompt:
            prompt = prompt.replace("{boss_title}", boss_title)
        # UI 设计模板系统注入
        if "{seed_template_path}" in prompt:
            prompt = self._inject_ui_templates(prompt, role)
            logger.info("[Action:%s] UI模板注入后 prompt 长度=%d, platform=%s, ds=%s",
                        self.name, len(prompt),
                        getattr(role, '_ui_platform', 'UNSET'),
                        getattr(role, '_ui_design_system', 'UNSET'))
        if "{chat_style}" in prompt:
            locale = getattr(role, '_locale', None)
            chat_style = ""
            if locale:
                chat_style = locale.get("chat_style", "")
            if not chat_style:
                chat_style = (
                    "## 回复规则\n"
                    f"- 称呼用户为「{boss_title}」\n"
                    "- 仔细阅读对话记录，延续之前的讨论，不重复已回答的问题\n"
                    "- 遇到模糊需求追问具体细节\n"
                    "- 遇到风险或需求冲突主动提示\n"
                    "- 有专业判断，会提出建议，但尊重客户最终决定\n"
                    "- 讨论中可以主动提出方案草案，让客户确认后再启动开发"
                )
            else:
                chat_style = chat_style.replace("{boss_title}", boss_title)
            prompt = prompt.replace("{chat_style}", chat_style)

        # 确保 prompt 包含项目工作目录（Reviewer/QA 的 context 可能不含此信息）
        if "## 项目工作目录\n" not in prompt and getattr(role, '_workspace', None):
            prompt = f"## 项目工作目录\n{role._workspace}\n\n{prompt}"
        system_prompt = role.build_system_prompt()

        router = role._runtime_router
        if self.use_cheap_model and getattr(router, '_cheap_provider', None) and getattr(router, '_cheap_model', None):
            try:
                cheap_spec = f"{router._cheap_provider}:{router._cheap_model}"
                router = router.clone_with_primary(cheap_spec)
                logger.info("[Action:%s] 使用 cheap 模型: %s", self.name, cheap_spec)
            except Exception as e:
                logger.warning("[Action:%s] cheap 模型切换失败，回退默认: %s", self.name, e)
        elif getattr(role, 'model_override', None):
            try:
                router = router.clone_with_primary(role.model_override)
                logger.info("[Action:%s] 使用角色专属模型: %s", self.name, role.model_override)
            except Exception as e:
                logger.warning("[Action:%s] 角色模型切换失败，回退默认: %s", self.name, e)

        engine = AgentEngine(
            router=router,
            system_prompt=system_prompt,
            max_tool_rounds=self.max_tool_rounds or (1 if self.tools_filter is not None and not self.tools_filter else role.max_tool_rounds),
            skip_grounding=True,
        )

        registry = role._runtime_registry
        if registry:
            filters = self.tools_filter if self.tools_filter is not None else role.tools_filter
            auto_approve = _has_workspace_guard(prompt)
            if filters:
                for tool in registry.list_tools():
                    if tool.category in filters:
                        engine.register_tool(
                            name=tool.name,
                            description=tool.description,
                            parameters=tool.parameters,
                            handler=tool.handler,
                            requires_approval=False if auto_approve else tool.requires_approval,
                        )

        _apply_workspace_guard(engine, prompt)

        read_total = 0
        read_fail = 0
        if self.name in ("CodeReview", "QATest"):
            read_tool = engine._tools.get("read_file")
            if read_tool and read_tool.handler:
                _orig_read = read_tool.handler
                async def _read_monitor_fn(orig=_orig_read, **kwargs):
                    nonlocal read_total, read_fail
                    read_total += 1
                    result = await orig(**kwargs)
                    if isinstance(result, str) and ("不存在" in result or "No such file" in result or "FileNotFoundError" in result):
                        read_fail += 1
                    return result
                read_tool.handler = _read_monitor_fn

        write_count = 0
        terminal_count = 0
        if self.name == "WriteCode":
            for tool_name in ("write_file", "edit_file"):
                tool = engine._tools.get(tool_name)
                if tool and tool.handler:
                    original_fn = tool.handler
                    async def _counting_fn(orig=original_fn, **kwargs):
                        nonlocal write_count
                        result = await orig(**kwargs)
                        if not (isinstance(result, str) and "禁止" in result):
                            write_count += 1
                        return result
                    tool.handler = _counting_fn
            term_tool = engine._tools.get("run_terminal")
            if term_tool and term_tool.handler:
                _orig_term = term_tool.handler
                async def _term_counting_fn(orig=_orig_term, **kwargs):
                    nonlocal terminal_count, write_count
                    result = await orig(**kwargs)
                    terminal_count += 1
                    cmd = kwargs.get("command", "")
                    _write_indicators = ("cat >", "cat <<", "echo ", "tee ", "printf ",
                                         "> ", ">> ", "sed -i", "cp ", "mv ", "heredoc",
                                         "write_text", "open(", "with open")
                    if any(p in cmd for p in _write_indicators):
                        write_count += 1
                    return result
                term_tool.handler = _term_counting_fn

        try:
            if self.name == "WriteUIDesign":
                logger.info("[Action:WriteUIDesign] 最终prompt长度=%d, seed_path=%s, layouts_path=%s",
                            len(prompt), "seed_template_path" not in prompt,
                            "layouts_path" not in prompt)
            result = await engine.run_turn(prompt)
            content = result.content if hasattr(result, "content") else str(result)
            if self.name == "WriteUIDesign":
                logger.info("[Action:WriteUIDesign] LLM返回长度=%d, 前300字=%s", len(content), content[:300])
        except Exception as e:
            logger.error("[Action:%s] 执行失败: %s", self.name, e)
            return f"[错误] {self.name} 执行失败: {e}"

        if self.name == "WriteCode" and write_count == 0:
            content = (
                "[错误] write_file 未被调用，代码没有写入磁盘。\n"
                "你必须通过 write_file 工具将每个文件写入项目工作目录。\n"
                "只在回复文本中输出代码是无效的。\n"
                f"本次执行了 {terminal_count} 次 run_terminal，但没有任何文件被写入。\n"
                "请重新执行，确保用 write_file 写入所有代码文件。"
            )
            logger.warning("WriteCode 完成但 write_file 未被调用 (terminal_count=%d)", terminal_count)

        if self.name in ("CodeReview", "QATest") and read_total > 0 and read_fail == read_total:
            logger.warning(
                "[%s] read_file 全部失败 (%d/%d)，文件可能不存在或 workspace 路径错误",
                self.name, read_fail, read_total,
            )
            content += (
                f"\n\n[WARNING] read_file 调用全部失败（{read_fail}/{read_total} 次返回文件不存在）。"
                "可能原因：workspace 路径配置错误或项目文件未生成。"
            )

        return content


# ── 内置 Actions ─────────────────────────────────────────────

USER_REQUIREMENT = Action(
    name="UserRequirement",
    description="用户原始需求",
)

EVALUATE_REQUIREMENT = Action(
    name="EvaluateRequirement",
    description="评估需求是否足够清晰，决定是否启动流水线",
    use_cheap_model=True,
    prompt_template=(
        "你是专业 PM，{boss_title}刚给了一个开发需求。你要判断这个需求是否足够清晰可以启动开发流水线。\n\n"
        "## 判断标准\n"
        "- 需求必须明确说了要做什么东西（不能只有一个名字，比如「小记」「商城」不算清晰）\n"
        "- 如果对话上下文里之前讨论过细节，可以结合上下文理解\n"
        "- 不需要完美，但至少要知道核心功能是什么\n"
        "- 必须有明确的项目名称。如果用户没说项目叫什么，你必须追问确认\n"
        "- 如果用户在讨论中逐步明确了需求（即使没说「开干」），只要需求清晰+有项目名，就可以 READY\n"
        "- 如果用户明确说了「开干」「开始开发」等指令，即使需求不够完美也应该 READY（结合上下文补全）\n\n"
        "## 输出格式（严格遵守）\n"
        "第一行必须是 READY 或 NEED_CLARIFY\n"
        "如果 READY：\n"
        "- 第二行必须是 PROJECT_NAME: 英文项目名（简短、kebab-case，如 ai-news-digest、smart-calculator）。如果用户说的是中文名，翻译为英文。\n"
        "- 第三行必须是 NEEDS_PROTOTYPE: YES 或 NO（判断标准：如果需求涉及用户界面/前端页面/小程序/Web应用/H5/App，写 YES；如果是纯后端/CLI/脚本/API/数据处理，写 NO）\n"
        "- 第四行起简述你理解的核心需求（2-3句话）\n"
        "如果 NEED_CLARIFY：第二行起礼貌地向{boss_title}提出具体问题（专业、简洁）\n"
        "- 如果用户没有给项目名称，必须追问项目名称\n"
        "- 如果需求明显不合理或自相矛盾，委婉说出你的顾虑\n"
        "- 直接问关键问题，不要客套\n\n"
        "{context}"
    ),
    tools_filter=[],
)

WRITE_PRD = Action(
    name="WritePRD",
    description="编写产品需求文档",
    prompt_template=(
        "你的任务：根据需求编写产品需求文档。\n"
        "直接输出 Markdown 格式 PRD，不使用任何工具，不读取文件。\n"
        "不过度设计，只覆盖需求本身。\n"
        "所有需求细节必须明确，不允许留待确认事项。如果某些细节不清晰，"
        "根据行业最佳实践和上下文做出合理决策，直接写入 PRD。\n\n"
        "## 输出格式（严格遵守）\n\n"
        "# 产品需求文档：[项目名]\n\n"
        "## 1. 项目概述\n"
        "[一段话描述产品定位和核心价值]\n\n"
        "## 2. 目标用户\n"
        "[用户画像，1-3 类]\n\n"
        "## 3. 功能需求\n"
        "| 优先级 | 功能模块 | 描述 | 验收标准 |\n"
        "|--------|----------|------|----------|\n"
        "| P0 | ... | ... | ... |\n\n"
        "## 4. 非功能需求\n"
        "- 性能：[要求]\n"
        "- 安全：[要求]\n"
        "- 兼容性：[要求]\n\n"
        "## 5. 技术约束\n"
        "[平台、语言、框架限制]\n\n"
        "---\n"
        "如果上下文包含「项目工作目录」，在开头注明路径。\n\n"
        "## 需求\n{context}"
    ),
    tools_filter=[],
)

WRITE_PROTOTYPE = Action(
    name="WritePrototype",
    description="生成低保真原型页面",
    prompt_template=(
        "你的任务：根据 PRD 为每个核心页面生成低保真原型 HTML 文件。\n\n"
        "## 输出规范\n"
        "- 使用 HTML + Tailwind CSS（通过 CDN 引入：<script src=\"https://cdn.tailwindcss.com\"></script>）\n"
        "- 每个页面一个独立 HTML 文件，放在 prototypes/ 目录下\n"
        "- 低保真风格：灰色背景色块(bg-gray-200)、线框边框(border)、占位文字\n"
        "- 包含页面间的导航链接（相对路径）\n"
        "- 用 HTML title 属性标注每个区域的功能说明\n"
        "- {prototype_layout}\n\n"
        "## 文件命名\n"
        "prototypes/index.html — 首页/入口\n"
        "prototypes/<page-name>.html — 各子页面\n\n"
        "## 增量规则\n"
        "- 先用 read_file 检查 prototypes/ 目录下已有哪些 HTML 文件\n"
        "- 已有的页面**不要重新生成**，只生成缺失的页面\n"
        "- 如果所有页面都已存在，直接报告完成即可\n\n"
        "## 重要\n"
        "- 必须使用 write_file 工具写入所有文件\n"
        "- 每个 HTML 文件必须是完整可独立打开的（包含 <html><head><body>）\n"
        "- 不需要写 JS 交互逻辑，只展示页面结构和布局\n\n"
        "## PRD\n{context}"
    ),
    tools_filter=["code", "file"],
    max_tool_rounds=15,
)

WRITE_UI_DESIGN = Action(
    name="WriteUIDesign",
    description="基于模板系统生成高保真 UI 设计稿（组合模式，非自由创作）",
    prompt_template=(
        "你的任务：基于预制模板系统，组合生成高保真 UI 设计页面。\n"
        "你不需要从零写 CSS——所有样式已在 seed template 中定义好。\n"
        "你的工作是：选择布局 → 填入内容 → 自检质量。\n\n"
        "# ═══════════════════════════════════════\n"
        "# 设计系统（Design System）\n"
        "# ═══════════════════════════════════════\n\n"
        "{design_system}\n\n"
        "# ═══════════════════════════════════════\n"
        "# 模板文件（用 read_file 按需读取）\n"
        "# ═══════════════════════════════════════\n\n"
        "以下文件包含 CSS class 系统和布局库，在 Step 2 时用 read_file 读取：\n"
        "- **Seed Template**: `{seed_template_path}` — 所有 CSS class 定义\n"
        "- **布局库**: `{layouts_path}` — 可用的布局片段\n"
        "- **质量自检清单**: `{checklist_path}`\n\n"
        "**绝对不要**自己写 `<style>` 块或 inline style（除了调整间距 margin/padding）。\n"
        "**不引入外部 CDN。** 不加 Tailwind、不加 Bootstrap、不加 Google Fonts。\n\n"
        "# ═══════════════════════════════════════\n"
        "# 工作流程\n"
        "# ═══════════════════════════════════════\n\n"
        "## Step 0 — 读取原型图（逐页处理，避免 context 过长）\n"
        "用 read_file 读取 prototypes/ 目录下的第一个 HTML 文件，记录：\n"
        "- 页面名称和文件名\n"
        "- 核心布局结构（table/card/grid/list）\n"
        "- 关键功能组件（按钮、表单、批量操作栏、筛选器等）\n"
        "- 数据展示方式（表格行数、卡片列数等）\n\n"
        "然后立即为该页面生成 UI 设计并 write_file 写入。\n"
        "写完后再 read_file 下一个原型文件，重复此过程。\n"
        "**逐页处理：读一个原型 → 写一个 UI 设计 → 读下一个。**\n\n"
        "**跳过以下文件（不是页面原型）：prototype-report.html、任何超过 50KB 的文件。**\n"
        "**原型图中有多少个页面，UI 设计就必须输出多少个页面，一个不能少。**\n"
        "**原型图用 table 布局的，UI 设计必须用 table；原型图有删除按钮的，UI 设计必须有删除按钮。**\n"
        "**不允许擅自更改原型图的布局结构或删减功能组件。**\n\n"
        "## Step 1 — 确认页面列表\n"
        "先用 read_file 列出 prototypes/ 目录内容，确认总共有多少个页面。\n"
        "然后按顺序逐个处理每个页面（Step 0 的循环）。\n\n"
        "## Step 2 — 读取 seed template + 注入 design tokens\n"
        "用 read_file 读取 seed template 文件（路径见上方）。\n"
        "每个页面从 seed template 开始。将 `:root` 中的 6 个变量替换为\n"
        "上方 Design System 定义的色值。替换 `<title>` 和导航文字。\n\n"
        "## Step 3 — 填入内容\n"
        "根据当前原型页面的结构，直接在 seed 的内容区域填入对应的 HTML。\n"
        "**严格按照原型图的布局和组件结构**，不要自由发挥。\n"
        "**不要用占位文字**——如果某个位置没有真实内容，删掉那个元素。\n\n"
        "## Step 3.5 — 交互设计\n"
        "为每个页面补充交互状态和行为：\n"
        "- 按钮：hover/active/disabled 三态样式（用 CSS :hover/:active/[disabled]）\n"
        "- 表单：focus 高亮、校验错误态（红色边框+提示文字）\n"
        "- 列表/卡片：空状态展示（无数据时的友好提示）、加载态（skeleton）\n"
        "- 导航：当前页 active 高亮\n"
        "- 用 HTML 注释 `<!-- interaction: 描述 -->` 标注复杂交互行为\n\n"
        "## Step 4 — 自检\n"
        "对照上方的质量自检清单逐项检查。P0 项必须全部通过。\n"
        "不通过的项目立即修改后重新 write_file。\n\n"
        "## Step 5 — 输出\n"
        "用 write_file 工具将每个页面写入 `ui-designs/` 目录。\n"
        "文件名用英文小写+连字符（如 `home.html`, `product-detail.html`）。\n\n"
        "## 硬性规则\n"
        "- **不要确认、不要提问、不要输出规划文字。直接用 write_file 写入文件。**\n"
        "- **不写 CSS。** 所有样式来自 seed 的 class。唯一允许的 inline style 是间距调整。\n"
        "- **不引入外部 CDN。** 不加 Tailwind、不加 Bootstrap、不加 Google Fonts。\n"
        "- **不用 emoji 做图标。** 用 SVG 或 seed 中的 class。\n"
        "- **accent 色每页最多 2 次。** 主按钮 + 一个强调元素。\n"
        "- **每个页面是完整独立的 HTML 文件。** 包含完整的 `<!doctype html>` 到 `</html>`。\n"
        "- **必须先 read_file 读取 prototypes/ 下每个文件，再开始设计。**\n"
        "- **页面数量必须与 prototypes/ 完全一致。** 原型有 N 个页面就输出 N 个。\n"
        "- **严禁更改原型图的布局类型。** 原型用 table 就用 table，原型用 card 就用 card。\n"
        "- **严禁删减原型图的功能组件。** 原型有批量操作/删除/编辑按钮，UI 设计必须保留。\n"
        "- **每个页面至少 3 条 `<!-- interaction: ... -->` 注释。**\n\n"
        "## PRD 与原型上下文\n{context}"
    ),
    tools_filter=["code", "file"],
    max_tool_rounds=30,
)

WRITE_DESIGN = Action(
    name="WriteDesign",
    description="编写技术设计方案",
    prompt_template=(
        "你的任务：根据 PRD 编写技术设计方案。\n"
        "直接输出结构化 Markdown，不使用任何工具，不读取文件。\n\n"
        "## 最重要规则\n"
        "如果上下文包含「现有项目技术栈」，你必须：\n"
        "1. 技术选型必须与现有技术栈一致（不能把 Python/Flask 项目改成 TypeScript/Next.js）\n"
        "2. 文件清单必须基于现有目录结构扩展，不能重新设计\n"
        "3. 这是迭代开发，不是从零开始\n\n"
        "## 输出格式（严格遵守）\n\n"
        "# 技术设计方案：[项目名]\n\n"
        "## 1. 技术选型\n"
        "| 类别 | 选择 | 理由 |\n"
        "|------|------|------|\n"
        "| 语言 | ... | ... |\n"
        "| 框架 | ... | ... |\n\n"
        "## 2. 架构概览\n"
        "[用 Mermaid 图或文字描述数据流：输入→处理→输出]\n\n"
        "## 3. 数据模型\n"
        "[表结构或数据结构定义]\n\n"
        "## 4. API 设计\n"
        "| 方法 | 路径 | 描述 | 请求体 | 响应 |\n"
        "|------|------|------|--------|------|\n\n"
        "## 5. 文件清单\n"
        "[每个要创建的文件，格式：`路径/文件名` — 职责说明]\n\n"
        "## 6. 模块拆分\n"
        "### MODULES\n"
        "- module: 模块名\n"
        "  files: [文件列表]\n"
        "  depends: [依赖模块]  # 无依赖写 []\n"
        "  description: 一句话说明\n\n"
        "## 7. 实现风险\n"
        "[可能的技术风险和应对方案]\n\n"
        "---\n\n"
        "## 设计规则\n"
        "- 文件清单是最重要的部分，Developer 严格按此创建文件\n"
        "- 每个模块不超过 5 个文件\n"
        "- 基础模块排前面，入口文件放最后一个模块\n"
        "- 项目很简单（≤5 文件）只需一个模块\n"
        "- 保持简洁，不过度抽象\n\n"
        "## 前端规则\n"
        "如果 PRD 提到「小程序」「微信小程序」，必须设计完整的小程序前端：\n"
        "- 使用微信小程序原生开发（app.json, pages/, components/）\n"
        "- 前端文件放在 miniapp/ 目录下\n"
        "- 后端 API 放在 src/ 目录下\n"
        "- 前后端都要在文件清单和模块拆分中体现\n"
        "如果 PRD 提到「Web应用」「网页」「H5」，必须设计前端页面。\n"
        "不要只做后端 API 而遗漏前端。\n\n"
        "如果上下文包含「用户本地开发环境」，技术选型必须兼容该环境版本。\n"
        "如果上下文包含「项目工作目录」，在开头注明路径。\n\n"
        "## PRD\n{context}"
    ),
    tools_filter=[],
)

SETUP_ENV = Action(
    name="SetupEnv",
    description="检测并安装项目所需的开发环境",
    prompt_template=(
        "你是 Developer，在写代码之前，先确保本地开发环境满足项目需求。\n\n"
        "## 当前环境信息\n"
        "（已在 context 中提供）\n\n"
        "## 你的任务\n"
        "1. 根据设计方案判断项目需要哪些运行时和工具\n"
        "2. 检查当前环境是否已安装（用 which/--version 验证）\n"
        "3. 如果缺失，自动安装：\n"
        "   - macOS: 用 brew install（如果有 brew）\n"
        "   - pip/pip3: 用 pip3 install\n"
        "   - npm: 用 npm install -g\n"
        "4. 如果项目需要 Python 虚拟环境，在项目目录下创建 .venv\n"
        "5. 不要安装不必要的东西，只装设计方案里明确需要的\n\n"
        "## 输出格式\n"
        "最后一行必须是以下之一：\n"
        "- ENV_READY — 环境已就绪，所有依赖已安装\n"
        "- ENV_READY_SKIP — 无需额外安装，当前环境已满足\n"
        "- ENV_FAIL — 无法安装某个关键依赖，说明原因\n\n"
        "{context}"
    ),
    tools_filter=["terminal"],
    max_tool_rounds=10,
)

WRITE_CODE = Action(
    name="WriteCode",
    description="编写代码（SOP：理解→编写→自验证→汇报）",
    prompt_template=(
        "你是专业开发工程师。严格按以下 SOP 逐步执行，每步完成后再进入下一步。\n\n"
        "## Step 1: 理解设计方案\n"
        "仔细阅读设计方案，列出要创建的文件清单和每个文件的职责。\n"
        "如果设计方案不清楚，在输出中标注疑问（但继续执行）。\n\n"
        "## Step 2: 逐文件编写代码\n"
        "按依赖顺序（基础模块先、入口文件最后），每个文件：\n"
        "1. 用 write_file 写入完整代码（file_path 必须是完整绝对路径）\n"
        "2. 写完后用 read_file 确认文件内容正确落盘\n"
        "原则：最少代码解决问题，不加未要求的功能，匹配项目现有风格。\n\n"
        "## Step 3: 自验证\n"
        "所有文件写完后，必须自行验证：\n"
        "1. 用 run_terminal 对每个 .py 文件执行 python3 -m py_compile（Node 项目用 node --check）\n"
        "2. 如果有语法错误，用 edit_file 修复后重新检查\n"
        "3. 如果有 requirements.txt，执行 pip3 install -r requirements.txt\n"
        "4. 如果项目有入口文件（app.py/main.py），尝试后台启动并 curl 验证\n"
        "5. 如果启动失败，查看错误日志，修复后重试（最多 3 次）\n"
        "6. 验证完成后，如果启动了服务，保持运行不要关闭\n\n"
        "## Step 4: 汇报\n"
        "简洁列出：已创建的文件清单、语法检查结果、启动验证结果、遗留问题（如有）。\n\n"
        "## 重要规则\n"
        "- 必须严格遵循设计方案的技术选型，不要自行替换框架\n"
        "- 前端页面必须严格还原 UI 设计图（如有 ui-designs/ 目录，先 read_file 读取对应设计稿再写代码）\n"
        "- 必须用 write_file 写文件，绝对不要只在回复文本中输出代码\n"
        "- 绝对不要用 run_terminal 写文件（cat >、echo >、tee、heredoc 等）\n"
        "- write_file 的 file_path 必须是完整绝对路径，以项目工作目录开头\n"
        "- 如果上下文包含「项目工作目录」，所有文件操作必须在该目录下\n"
        "- 后端代码写入 src/，前端代码按设计方案指定的目录（如 miniapp/、web/）\n"
        "- 配置文件放项目根目录\n"
        "- 如果项目目录下有 .port 文件，必须读取其中的端口号作为服务监听端口，不要自己选端口\n"
        "- 如果项目目录下有 .env 文件，用 os.environ 或 dotenv 读取配置\n"
        "- 绝对不要写入隐藏目录（如 .xjd-agent/）\n\n"
        "## 设计与需求\n{context}"
    ),
    tools_filter=["code", "file", "terminal"],
    max_tool_rounds=30,
)

CODE_REVIEW = Action(
    name="CodeReview",
    description="代码审查（SOP：读代码→对照设计→UI对照→质量审查→路由验证→报告）",
    prompt_template=(
        "你是资深代码审查员。按以下 SOP 执行审查：\n\n"
        "## Step 1: 读取源码\n"
        "用 read_file 逐个读取项目工作目录下的源码文件（src/ 目录）。\n"
        "不要只看 context 中的代码片段，要读完整文件确认实际内容。\n\n"
        "## Step 2: 对照设计方案审查\n"
        "检查：\n"
        "1. 技术选型是否与设计方案一致（框架、库必须完全匹配）\n"
        "2. 设计方案中的每个文件是否都已创建\n"
        "3. 核心接口/函数签名是否与设计方案匹配\n"
        "如果代码使用了设计方案中未指定的框架，必须 REJECTED。\n\n"
        "## Step 2.5: UI 设计稿强制对照（如有 ui-designs/ 目录，此步骤为必做项）\n"
        "如果项目有 ui-designs/ 目录：\n"
        "1. 用 read_file 读取 ui-designs/ 下的【每一个】HTML 文件\n"
        "2. 用 read_file 读取对应的实现文件（templates/ 或 src/ 下同名模板）\n"
        "3. 逐项对照以下清单（每项必须明确标注 PASS 或 FAIL）：\n"
        "   - 导航栏：链接数量、链接文本、链接顺序是否与设计稿一致\n"
        "   - 页面结构：主要 section 数量和排列顺序是否一致\n"
        "   - 组件完整性：设计稿中的表单/卡片/列表/按钮是否都已实现\n"
        "   - 文本内容：标题、描述文字是否与设计稿一致（允许合理缩写）\n"
        "   - 无多余 emoji：如果设计稿中某处无 emoji 但实现中加了 emoji，标记 FAIL\n"
        "   - 布局一致性：grid/flex 布局方式是否与设计稿匹配\n"
        "4. 判定规则：\n"
        "   - 任何一项属于「缺少导航项」「缺少整个 section」「布局结构错误」→ 必须 REJECTED\n"
        "   - 仅「文本微调」「颜色细微差异」→ 可以 APPROVED 但需备注\n\n"
        "## Step 3: 代码质量审查\n"
        "检查：\n"
        "1. 安全漏洞（注入、XSS、硬编码密钥等）\n"
        "2. 逻辑正确性（边界条件、错误处理）\n"
        "3. 是否过度工程（不需要的抽象、未要求的功能）\n\n"
        "## Step 3.5: 路由可达性验证\n"
        "如果是 web 服务且服务正在运行：\n"
        "- 用 terminal 执行 curl 测试每个页面路由，确认返回 200\n"
        "- 如果有路由返回 404/500，必须 REJECTED\n"
        "如果服务未运行或无 terminal 工具：\n"
        "- 检查代码中所有路由是否都有对应的 handler 函数\n"
        "- 检查蓝图/路由器是否正确注册到 app（注意 Python 包 vs 文件优先级）\n"
        "- 如果发现路由注册缺失，必须 REJECTED\n\n"
        "## Step 4: 输出审查报告\n"
        "格式：\n"
        "### UI 设计稿对照结果\n"
        "[逐项清单结果，每项标注 PASS/FAIL]\n\n"
        "### 代码质量\n"
        "[问题列表或「无问题」]\n\n"
        "### 路由验证\n"
        "[路由测试结果]\n\n"
        "### 结论\n"
        "最后一行必须是：APPROVED 或 REJECTED + 具体原因\n\n"
        "## 代码变更\n{context}"
    ),
    tools_filter=["code", "file", "terminal"],
    max_tool_rounds=15,
)

CODE_REVIEW_FOLLOWUP = Action(
    name="CodeReviewFollowup",
    description="返工后复审（只验证修复，不全量审查）",
    prompt_template=(
        "你是代码审查员，正在进行返工后的复审。\n\n"
        "## 重要：这是复审，不是全量审查\n"
        "- 只验证上一轮指出的问题是否已正确修复\n"
        "- 只读取上一轮反馈中提到的文件\n"
        "- 不要审查其他文件，不要提出新的 style 建议\n"
        "- 只有以下情况才 REJECTED：\n"
        "  1. 上一轮的问题完全没有修复\n"
        "  2. 修复引入了安全漏洞或会导致崩溃的 bug\n"
        "- 其他情况一律 APPROVED（可附带非阻塞建议）\n\n"
        "{context}"
    ),
    tools_filter=["code", "file"],
    max_tool_rounds=5,
)

WRITE_TEST = Action(
    name="WriteTest",
    description="编写并运行测试（SOP：读源码→写测试→跑测试→修复→汇报）",
    prompt_template=(
        "你是测试工程师。按以下 SOP 执行：\n\n"
        "## Step 1: 读取源码\n"
        "用 read_file 读取项目工作目录下的源码文件，理解要测试什么。\n"
        "同时读取 PRD 中的验收标准，确保测试覆盖所有验收条件。\n\n"
        "## Step 2: 编写测试\n"
        "用 write_file 写入测试文件到项目工作目录的 tests/ 下。\n"
        "覆盖：正常路径、边界情况、验收标准中的每个条件。\n"
        "Python 项目用 pytest，Node 项目用 jest。\n\n"
        "## Step 3: 运行测试\n"
        "用 run_terminal 执行测试命令（python3 -m pytest tests/ -v 或 npx jest）。\n"
        "如果测试失败，分析原因：\n"
        "- 如果是测试代码本身的问题（import 错误、断言写错），用 edit_file 修复测试后重跑\n"
        "- 如果是源码的 bug，记录到报告中（不要修改源码）\n"
        "- 最多修复重跑 3 次\n\n"
        "## Step 4: 汇报\n"
        "贴出测试通过/失败的统计行。\n"
        "失败的用例列出名称和原因，通过的不用逐条列。\n"
        "总结不超过 10 行。\n\n"
        "## 代码与验收标准\n{context}"
    ),
    tools_filter=["code", "file", "terminal"],
    max_tool_rounds=15,
)

RUN_TEST = Action(
    name="RunTest",
    description="运行测试",
    prompt_template=(
        "你必须实际运行测试命令，不能只分析代码就说通过。\n\n"
        "## 步骤\n"
        "1. cd 到项目工作目录\n"
        "2. 找到测试文件（tests/ 目录下）\n"
        "3. 执行测试命令：\n"
        "   - Python 项目：python3 -m pytest tests/ -v\n"
        "   - Node 项目：npm test 或 npx jest\n"
        "4. 如果测试失败，分析原因并给出修复建议\n"
        "5. 贴出完整的测试运行输出（包含 passed/failed 统计行）\n\n"
        "## 禁止\n"
        "- 不要只看代码就说「测试应该能通过」\n"
        "- 不要编造测试结果\n"
        "- 必须有真实的命令执行输出\n"
        "- 不要说「安全策略拦截」「安全策略封锁」除非你真的看到了那个确切的错误信息\n"
        "- 如果某个命令失败，换一种方式重试（比如 python3 代替 python），不要直接放弃\n\n"
        "## 汇报风格\n"
        "简洁专业的测试报告：\n"
        "- 贴测试通过/失败的统计行即可，不要贴完整日志\n"
        "- 失败的用例列出名称和原因，通过的不用逐条列\n"
        "- 总结不超过 10 行\n\n"
        "## 测试信息\n{context}"
    ),
    tools_filter=["code", "terminal"],
)

VERIFY_RUN = Action(
    name="VerifyRun",
    description="验证代码可运行：安装依赖、语法检查、启动服务",
    prompt_template=(
        "你刚写完代码，现在必须验证它能跑起来。按以下步骤执行：\n\n"
        "## 第一步：进入项目目录\n"
        "cd 到项目工作目录，ls 看结构。\n\n"
        "## 第二步：安装依赖\n"
        "- 如果有 requirements.txt → pip3 install -r requirements.txt\n"
        "- 如果有 package.json → npm install\n"
        "- 如果没有依赖文件，跳过\n\n"
        "## 第三步：语法检查\n"
        "- Python 项目：对 src/ 下每个 .py 文件执行 python3 -m py_compile\n"
        "- Node 项目：对 src/ 下每个 .js 文件执行 node --check\n"
        "- 如果有语法错误，用 edit_file 修复后重新检查\n\n"
        "## 第四步：尝试启动\n"
        "- 找到入口文件（main.py / app.py / index.js）\n"
        "- 如果项目目录下有 .port 文件，读取其中的端口号作为服务端口，不要自己选端口\n"
        "- 用 nohup 后台启动，绑定 0.0.0.0\n"
        "- sleep 3 后 curl localhost:端口 验证\n"
        "- 如果启动失败，查看日志修复后重试（最多重试 3 次）\n"
        "- 如果不是 web 服务（如 CLI 工具、脚本），跳过启动，只做语法检查\n\n"
        "## 第四点五步：全路由验证（仅 web 服务启动成功后执行）\n"
        "如果服务已启动成功：\n"
        "- 在项目源码中搜索所有路由定义：\n"
        "  - Python: grep -rn '@app.route\\|@bp.route\\|@.*\\.route\\|add_url_rule' src/ *.py\n"
        "  - Node/Express: grep -rn 'router.get\\|router.post\\|app.get\\|app.post' src/\n"
        "- 对每个发现的 GET 路由路径，执行：\n"
        "  curl -s -o /dev/null -w '%{http_code}' http://localhost:端口/路径\n"
        "- GET 路由期望返回 200 或 302（重定向也算通过）\n"
        "- POST/PUT/DELETE 路由跳过（需要 body）\n"
        "- 如果任何 GET 路由返回 404 或 500：\n"
        "  1. 检查蓝图/路由器是否正确注册到 app\n"
        "  2. 检查是否有同名文件和包冲突（Python 中包优先于同名 .py 文件）\n"
        "  3. 尝试修复后重新验证\n"
        "- 如果修复后仍有路由返回 404/500，标记为 VERIFY_FAIL\n\n"
        "## 第四点六步：UI 设计稿对照检查（如有 ui-designs/ 目录）\n"
        "如果项目目录下有 ui-designs/ 目录：\n"
        "- 用 read_file 读取每个 ui-designs/*.html 文件\n"
        "- 用 read_file 读取对应的实现文件（templates/ 或 src/ 下同名 HTML/模板文件）\n"
        "- 对比以下结构元素：\n"
        "  1. 导航栏链接数量和文本是否一致\n"
        "  2. 页面主要 section 的 id/class 是否存在\n"
        "  3. 关键组件（表单、卡片、列表）是否都已实现\n"
        "  4. 设计稿中无 emoji 但实现中有 emoji → 标记为不一致\n"
        "- 输出对照报告（简洁，每个文件 1-2 行），标注「一致」或「不一致 + 原因」\n"
        "- 此步骤不影响 VERIFY_PASS/FAIL 判定，仅作为信息提供给 Reviewer\n\n"
        "## 第五步：汇报结果\n"
        "简洁汇报，不要贴完整日志，只说结论和关键信息。\n"
        "如果有 UI 设计稿对照报告，附在结论之前。\n"
        "最后一行必须是以下之一：\n"
        "- VERIFY_PASS — 服务已启动，所有 GET 路由验证通过\n"
        "- VERIFY_PASS_NO_SERVER — 不是 web 服务，语法检查通过\n"
        "- VERIFY_FAIL — 无法修复的问题（含路由返回 404/500），说明原因\n\n"
        "{context}"
    ),
    tools_filter=["code", "file", "terminal"],
    max_tool_rounds=25,
)

DEPLOY_PLAN = Action(
    name="DeployPlan",
    description="制定部署方案",
    prompt_template=(
        "你的任务：根据测试结果，制定部署方案。\n"
        "直接输出部署方案文本，不要执行任何命令。\n"
        "如果这是一个简单的本地脚本（不需要部署到服务器），直接回复：无需部署。\n"
        "否则包含：部署步骤、回滚方案、健康检查命令。\n"
        "方案要精炼，只列关键步骤，不要写长篇大论。\n\n"
        "## 部署信息\n{context}"
    ),
    tools_filter=[],
)

EXECUTE_DEPLOY = Action(
    name="ExecuteDeploy",
    description="执行部署",
    prompt_template=(
        "按照以下部署方案执行部署。\n"
        "如果方案说「无需部署」，直接回复：部署完成（无需操作）。\n"
        "否则每一步执行后验证，失败则回滚。\n\n"
        "## 部署规范\n"
        "- 如果项目目录下有 .port 文件，读取其中的端口号作为服务端口\n"
        "- 启动命令格式：nohup ... > {{项目目录}}/app.log 2>&1 & echo $! > {{项目目录}}/.pid\n"
        "- 启动后 sleep 3 && curl -s http://localhost:{{端口}} 验证\n"
        "- 如果验证失败，cat app.log 查看错误，修复后重试（最多 3 次）\n\n"
        "## 部署完成后必须汇报\n"
        "- 用 `ifconfig | grep inet` 获取本机局域网 IP\n"
        "- 汇报访问地址，格式：http://局域网IP:端口\n"
        "- 用 curl 验证服务可访问，附上验证结果\n"
        "- 没有给出访问地址 = 部署未完成\n"
        "- 汇报简洁，只说结论（成功/失败+地址），不要贴完整命令输出\n\n"
        "## 部署方案\n{context}"
    ),
    tools_filter=["system", "terminal", "network"],
)

CHAT_REPLY = Action(
    name="ChatReply",
    description="回复用户消息（待命模式）",
    use_cheap_model=True,
    prompt_template=(
        "你是项目产品经理，在飞书群里与客户沟通。\n\n"
        "{chat_style}\n\n"
        "## 身份\n"
        "- 7×24 在线，不存在「下班」「明天再说」\n"
        "- 不用时间推脱工作\n\n"
        "## 禁止\n"
        "- 不能执行任何操作（写代码、部署、查文件、启动服务）\n"
        "- 不能暗示正在产出（「马上发给你」「稍等」「80%了」）\n"
        "- 不能暗示会自主行动（「我去查」「我去改」「直接动手」）\n"
        "- 不能启动流水线，只有用户发明确开发指令后系统自动触发\n"
        "- 不编造数据、进度、完成情况\n"
        "- 不声称服务正在运行，除非刚执行过启动并验证\n"
        "- 不确定项目当前状态时如实说不确定，不要猜测\n"
        "- 任务记录的「done」只表示流水线跑完，不代表服务在运行\n\n"
        "## 引导用户\n"
        "- 用户说「验收」「看效果」→ 告知：请说「跑起来」或「部署一下」，系统会安排运维启动\n"
        "- 用户说「直接改」→ 告知：请发具体开发指令，系统会自动启动团队执行\n"
        "- 用户要求开始工作 → 告知：说「开干」「确认」即可启动流水线\n"
        "- 用户讨论需求 → 积极参与，帮理清思路，提出专业建议\n\n"
        "{context}"
    ),
    tools_filter=[],
)

QUICK_TASK = Action(
    name="QuickTask",
    description="快速执行用户指定的操作任务（不走完整流水线）",
    prompt_template=(
        "{boss_title}给了一个操作指令，你需要直接执行（不是写新功能，不需要走开发流程）。\n"
        "使用工具完成任务，执行完如实汇报结果。\n"
        "如果执行失败，如实说明原因，不要编造成功。\n"
        "如果任务超出你的能力范围（比如没有服务器权限），诚实说明。\n\n"
        "## 第一步：直接进入项目目录\n"
        "如果上下文包含「项目工作目录」，第一个命令必须是 cd 到那个目录，然后 ls 看结构。\n"
        "不要浏览其他目录，不要用 list_directory 逐层探索。直接进项目目录开始干活。\n\n"
        "## 允许的命令（重要！不要误判为被拦截）\n"
        "以下命令完全允许执行，不会被安全策略拦截：\n"
        "- python3、python、node、npm、pip、pip3\n"
        "- nohup、cp、mv、mkdir、cat、ls、cd、pwd\n"
        "- curl、wget、git、tar、chmod\n"
        "- 任何项目启动命令（flask run、uvicorn、npm start 等）\n\n"
        "只有以下命令会被拦截：kill/killall/pkill、rm -r /、shutdown/reboot、crontab、systemctl\n"
        "被拦截时的错误信息格式是：「Error: 命令被安全策略拦截」\n"
        "如果你没有看到这个确切的错误信息，说明命令没有被拦截！\n"
        "遇到其他错误（ModuleNotFoundError、ImportError、SyntaxError、端口占用等）是代码本身的问题，\n"
        "不是安全策略问题。请诊断真正的错误原因并修复。\n\n"
        "## 效率要求\n"
        "- 你只有 20 轮工具调用机会，必须高效利用\n"
        "- 第1轮：cd 到项目目录 + ls 看结构\n"
        "- 第2轮：找到入口文件（如 app.py、main.py、index.html），cat 看一下\n"
        "- 第3轮起：直接执行操作（安装依赖、启动服务等）\n"
        "- 多个独立操作可以合并到一条命令（用 && 连接）\n"
        "- 禁止用 list_directory 逐层浏览目录结构，直接用 ls 或 find\n\n"
        "## 启动服务的注意事项\n"
        "- 服务必须绑定 0.0.0.0（不是 127.0.0.1），这样局域网内其他设备才能访问\n"
        "  例如：`flask run --host=0.0.0.0` 或 `python3 -m http.server 8080 --bind 0.0.0.0`\n"
        "- 启动任何长期运行的服务必须用 nohup 或后台方式：`nohup python3 app.py > /tmp/app.log 2>&1 &`\n"
        "- 启动后必须等几秒（sleep 3），然后用 curl 验证服务确实在运行\n"
        "- 如果 curl 失败，查看日志（cat /tmp/app.log）找原因并修复\n"
        "- 常见问题：缺少依赖 → 用 pip3 install 安装；端口占用 → 换端口\n"
        "- 不要用 kill/killall/pkill 命令，直接启动新进程即可\n"
        "- 如果端口被占用，换一个端口启动\n\n"
        "## 汇报要求（必须遵守）\n"
        "- 启动服务后，必须汇报实际的访问地址，格式：http://本机IP:端口\n"
        "- 用 `ifconfig | grep inet` 或 `hostname -I` 获取本机局域网 IP\n"
        "- 汇报格式示例：「服务已启动，访问地址：http://192.168.1.100:8080」\n"
        "- 如果 curl 验证成功，附上 curl 结果摘要\n"
        "- 如果是部署/启动类任务，没有给出访问地址 = 任务未完成\n\n"
        "## 绝对禁止\n"
        "- 不要说「安全策略拦截」「安全策略封锁」除非你真的看到了那个确切的错误信息\n"
        "- 不要编造命令执行结果，必须基于工具返回的真实输出\n"
        "- 不要放弃，遇到错误就修复，尽力完成\n"
        "- 不要浪费轮次在 list_directory 上，直接用 shell 命令操作\n\n"
        "回复简短专业，汇报关键结果即可。\n\n"
        "{context}"
    ),
    tools_filter=["code", "file", "terminal", "system"],
    max_tool_rounds=20,
)

FIX_CODE = Action(
    name="FixCode",
    description="根据审查意见修复代码（patch 模式，不重写）",
    prompt_template=(
        "你收到了代码审查反馈，需要修复指出的问题。\n\n"
        "## 重要：只修改有问题的部分\n"
        "- 使用 edit_file 修改现有文件中有问题的代码，不要用 write_file 重写整个文件\n"
        "- 只改审查意见明确指出的具体问题，不要改动没有问题的代码\n"
        "- 如果审查意见指出缺少某个文件，用 write_file 创建它\n"
        "- 修改后用 read_file 确认改动正确\n"
        "- write_file/edit_file 的 file_path 必须是完整绝对路径\n"
        "- 绝对不要用 run_terminal 写文件\n\n"
        "## 审查反馈\n{context}"
    ),
    tools_filter=["code", "file"],
    max_tool_rounds=10,
)

ALL_ACTIONS: dict[str, Action] = {
    a.name: a for a in [
        USER_REQUIREMENT, EVALUATE_REQUIREMENT, WRITE_PRD,
        WRITE_PROTOTYPE, WRITE_UI_DESIGN, WRITE_DESIGN,
        SETUP_ENV, WRITE_CODE, VERIFY_RUN, CODE_REVIEW, CODE_REVIEW_FOLLOWUP, FIX_CODE,
        WRITE_TEST, RUN_TEST,
        DEPLOY_PLAN, EXECUTE_DEPLOY, CHAT_REPLY, QUICK_TASK,
    ]
}
