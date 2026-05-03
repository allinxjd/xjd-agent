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


@dataclass
class Action:
    """角色可执行的原子操作."""

    name: str
    description: str = ""
    prompt_template: str = ""
    tools_filter: Optional[list[str]] = None
    max_tool_rounds: Optional[int] = None

    async def run(self, context: str, role: CompanyRole) -> str:
        from agent.core.engine import AgentEngine

        prompt = self.prompt_template.format(context=context) if self.prompt_template else context
        system_prompt = role.build_system_prompt()

        engine = AgentEngine(
            router=role._runtime_router,
            system_prompt=system_prompt,
            max_tool_rounds=self.max_tool_rounds or (1 if self.tools_filter is not None and not self.tools_filter else role.max_tool_rounds),
            skip_grounding=True,
        )

        registry = role._runtime_registry
        if registry:
            filters = self.tools_filter if self.tools_filter is not None else role.tools_filter
            if filters:
                for tool in registry.list_tools():
                    if tool.category in filters:
                        engine.register_tool(
                            name=tool.name,
                            description=tool.description,
                            parameters=tool.parameters,
                            handler=tool.handler,
                            requires_approval=tool.requires_approval,
                        )

        _apply_workspace_guard(engine, prompt)

        write_count = 0
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

        try:
            result = await engine.run_turn(prompt)
            content = result.content if hasattr(result, "content") else str(result)
        except Exception as e:
            logger.error("[Action:%s] 执行失败: %s", self.name, e)
            return f"[错误] {self.name} 执行失败: {e}"

        if self.name == "WriteCode" and write_count == 0:
            content = (
                "[错误] write_file 未被调用，代码没有写入磁盘。\n"
                "你必须通过 write_file 工具将每个文件写入项目工作目录。\n"
                "只在回复文本中输出代码是无效的。"
            )
            logger.warning("WriteCode 完成但 write_file 未被调用")

        return content


# ── 内置 Actions ─────────────────────────────────────────────

USER_REQUIREMENT = Action(
    name="UserRequirement",
    description="用户原始需求",
)

EVALUATE_REQUIREMENT = Action(
    name="EvaluateRequirement",
    description="评估需求是否足够清晰，决定是否启动流水线",
    prompt_template=(
        "你是专业 PM，老板刚给了一个开发需求。你要判断这个需求是否足够清晰可以启动开发流水线。\n\n"
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
        "- 第二行必须是 PROJECT_NAME: 项目名称（用户确认过的名字，或用户消息中明确提到的名字）\n"
        "- 第三行起简述你理解的核心需求（2-3句话）\n"
        "如果 NEED_CLARIFY：第二行起用群聊口吻向老板提出具体问题（像真人PM在群里追问那样，简短有力，带表情）\n"
        "- 如果用户没有给项目名称，必须追问「老板，这个项目叫什么名字？」\n"
        "- 如果需求明显不合理或自相矛盾，直接说出你的顾虑\n"
        "- 不要客套，直接问关键问题\n\n"
        "{context}"
    ),
    tools_filter=[],
)

WRITE_PRD = Action(
    name="WritePRD",
    description="编写产品需求文档",
    prompt_template=(
        "你的任务：根据需求编写一份简洁的 PRD（产品需求文档）。\n"
        "直接输出 PRD 文本，不要使用任何工具，不要读取文件。\n"
        "必须包含：功能描述、用户故事、验收标准（可测试的条件列表）。\n"
        "不要过度设计，只覆盖需求本身。\n"
        "如果上下文包含「项目工作目录」，请在 PRD 开头注明项目路径。\n\n"
        "## 需求\n{context}"
    ),
    tools_filter=[],
)

WRITE_DESIGN = Action(
    name="WriteDesign",
    description="编写技术设计方案",
    prompt_template=(
        "你的任务：根据 PRD 编写技术设计方案。\n"
        "直接输出设计文本，不要使用任何工具，不要读取文件。\n\n"
        "## 必须包含\n"
        "1. 技术选型（语言、框架、库）\n"
        "2. 具体文件清单 — 列出每个要创建的文件，格式：\n"
        "   - `src/文件名` — 职责说明\n"
        "3. 核心接口/函数签名\n"
        "4. 数据流（输入→处理→输出）\n\n"
        "文件清单是最重要的部分，Developer 会严格按照这个清单创建文件。\n"
        "保持简洁，不要过度抽象。\n"
        "如果上下文包含「用户本地开发环境」，技术选型必须兼容该环境版本。\n\n"
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
    description="编写代码",
    prompt_template=(
        "根据以下设计方案和需求，编写代码实现。\n"
        "原则：最少代码解决问题，不加未要求的功能，匹配项目现有风格。\n\n"
        "## 重要：必须严格遵循设计方案的技术选型\n"
        "设计方案中指定了什么语言、框架、库，你就必须用什么。\n"
        "例如设计方案写了 aiohttp，你就不能用 FastAPI；写了 Flask，你就不能用 Django。\n"
        "技术选型是设计阶段的决策，不是你的自由发挥空间。\n"
        "如果你认为设计方案的选型有问题，在代码开头注释说明，但仍然按设计方案实现。\n\n"
        "## 重要：必须使用 write_file 工具写入文件\n"
        "你必须通过 write_file 工具将代码写入磁盘。\n"
        "绝对不要只在回复文本中输出代码 — 那样文件不会被创建。\n"
        "绝对不要用 run_terminal 写文件（cat >、echo >、tee、heredoc 等），那样不会被系统记录。\n"
        "每个文件都必须调用一次 write_file。\n"
        "write_file 的 file_path 参数必须是完整的绝对路径，以项目工作目录开头。\n"
        "例如：如果项目工作目录是 /home/user/projects/myapp，\n"
        "那么写入 src/main.py 时 file_path 必须是 /home/user/projects/myapp/src/main.py。\n\n"
        "如果上下文包含「项目工作目录」，所有文件操作必须在该目录下。\n"
        "代码写入 src/，配置文件放项目根目录。\n"
        "如果上下文包含「用户本地开发环境」，代码必须兼容该环境的 Python/Node 版本。\n"
        "如果项目目录下有 .env 文件，代码中应使用 os.environ 或 dotenv 读取配置，不要硬编码密钥。\n"
        "绝对不要写入隐藏目录（如 .xjd-agent/）。\n\n"
        "## 设计与需求\n{context}"
    ),
    tools_filter=["code", "file"],
)

CODE_REVIEW = Action(
    name="CodeReview",
    description="代码审查",
    prompt_template=(
        "你的任务：审查以下代码变更。\n"
        "直接输出审查意见，不要使用任何工具。\n"
        "检查：\n"
        "1. 技术选型是否与设计方案一致（框架、库必须完全匹配，不允许自行替换）\n"
        "2. 每行改动是否都能追溯到需求\n"
        "3. 安全漏洞（注入、XSS、硬编码密钥等）\n"
        "4. 逻辑正确性\n"
        "5. 是否过度工程\n\n"
        "如果代码使用了设计方案中未指定的框架（如设计写 aiohttp 但代码用 FastAPI），必须 REJECTED。\n\n"
        "## 输出风格\n"
        "你在飞书群里汇报，像真正的审查员一样简洁专业：\n"
        "- 总结不超过 5 行，只说关键发现\n"
        "- 没问题就一句话带过，有问题才展开\n"
        "- 最后一行必须是：APPROVED 或 REJECTED + 原因\n\n"
        "## 代码变更\n{context}"
    ),
    tools_filter=[],
)

WRITE_TEST = Action(
    name="WriteTest",
    description="编写测试",
    prompt_template=(
        "根据以下代码和验收标准，编写测试。\n"
        "原则：先写测试复现问题/验证功能，再确认通过。\n"
        "覆盖正常路径和边界情况。\n"
        "如果上下文包含「项目工作目录」，测试文件写入该目录的 tests/ 下。\n\n"
        "## 代码与验收标准\n{context}"
    ),
    tools_filter=["code", "file", "terminal"],
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
        "你在飞书群里汇报，像真正的 QA 一样简洁：\n"
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
        "- 用 nohup 后台启动，绑定 0.0.0.0\n"
        "- sleep 3 后 curl localhost:端口 验证\n"
        "- 如果启动失败，查看日志修复后重试（最多重试 3 次）\n"
        "- 如果不是 web 服务（如 CLI 工具、脚本），跳过启动，只做语法检查\n\n"
        "## 第五步：汇报结果\n"
        "简洁汇报，不要贴完整日志，只说结论和关键信息。\n"
        "最后一行必须是以下之一：\n"
        "- VERIFY_PASS — 服务已启动，curl 验证通过\n"
        "- VERIFY_PASS_NO_SERVER — 不是 web 服务，语法检查通过\n"
        "- VERIFY_FAIL — 无法修复的问题，说明原因\n\n"
        "{context}"
    ),
    tools_filter=["code", "file", "terminal"],
    max_tool_rounds=15,
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
    prompt_template=(
        "你当前处于待命模式，在飞书群里和老板聊天。\n\n"
        "## 回复规则\n"
        "- 称呼用户为「老板」\n"
        "- 像真正的员工在群里聊天一样，自然、有温度、有自己的性格\n"
        "- 直接回应内容，绝对不要复述或重复用户说的话\n"
        "- 回复简短有力，一两句话搞定，群聊风格\n"
        "- 善用表情但不堆砌，比如 😎🫡👀🔥💪😂🤔\n"
        "- 仔细阅读对话记录，延续之前的讨论，不要重复问已回答的问题\n\n"
        "## 你的身份\n"
        "- 你是 AI 员工，7×24 小时在线，没有「下班」「睡觉」「明天再说」的概念\n"
        "- 可以关心老板作息（「老板注意休息啊」），但你自己永远在线，不能拿时间当借口\n"
        "- 不要说「明天一早给你」「下次再聊」之类用时间推脱工作的话\n\n"
        "## 专业 PM 态度\n"
        "- 你是专业 PM，不是只会说「好的收到」的传话筒\n"
        "- 遇到模糊需求要主动追问：「这个具体是指什么？」「目标用户是谁？」\n"
        "- 遇到不合理需求要直说顾虑：「老板这个可能有问题」「建议换个方案」\n"
        "- 遇到需求冲突要指出：「这和之前说的XX矛盾了，以哪个为准？」\n"
        "- 有自己的专业判断，敢于提出不同意见，但尊重老板最终决定\n\n"
        "## 绝对禁止\n"
        "- 你现在只能聊天，不能实际写文档、写代码或执行任何任务\n"
        "- 绝对不要假装你正在写 PRD、做方案、写代码或有任何产出物\n"
        "- 不要说「马上发给你」「稍等我整理一下」「80%了」之类暗示你有产出的话\n"
        "- 不要编造任何数据、进度百分比、完成情况\n"
        "- 不要声称服务/项目正在运行、已部署、可以访问，除非你刚刚亲自执行了启动命令并验证\n"
        "- 任务记录里的「done」只表示流水线跑完了，不代表服务在运行中\n"
        "- 如果老板问项目能不能打开/访问，诚实说你不确定运行状态，建议启动流水线重新部署\n"
        "- 如果老板要求你开始工作或交付成果，诚实告诉他：说一句「开干」「确认」「没问题了就开始」就能启动团队流水线\n"
        "- 如果老板在讨论需求，你应该积极参与讨论，帮他理清思路，提出专业建议\n"
        "- 讨论中可以主动提出方案草案，让老板确认后再启动开发\n"
        "- 如果其他角色（开发、运维等）在群里提了意见，你要综合考虑并给老板建议\n\n"
        "{context}"
    ),
    tools_filter=[],
)

QUICK_TASK = Action(
    name="QuickTask",
    description="快速执行用户指定的操作任务（不走完整流水线）",
    prompt_template=(
        "老板给了一个操作指令，你需要直接执行（不是写新功能，不需要走开发流程）。\n"
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
        "- 汇报格式示例：「服务已启动 ✅ 访问地址：http://192.168.1.100:8080」\n"
        "- 如果 curl 验证成功，附上 curl 结果摘要\n"
        "- 如果是部署/启动类任务，没有给出访问地址 = 任务未完成\n\n"
        "## 绝对禁止\n"
        "- 不要说「安全策略拦截」「安全策略封锁」除非你真的看到了那个确切的错误信息\n"
        "- 不要编造命令执行结果，必须基于工具返回的真实输出\n"
        "- 不要放弃，遇到错误就修复，尽力完成\n"
        "- 不要浪费轮次在 list_directory 上，直接用 shell 命令操作\n\n"
        "回复简短，群聊风格，汇报关键结果即可。\n\n"
        "{context}"
    ),
    tools_filter=["code", "file", "terminal", "system"],
    max_tool_rounds=20,
)

ALL_ACTIONS: dict[str, Action] = {
    a.name: a for a in [
        USER_REQUIREMENT, EVALUATE_REQUIREMENT, WRITE_PRD, WRITE_DESIGN,
        SETUP_ENV, WRITE_CODE, VERIFY_RUN, CODE_REVIEW, WRITE_TEST, RUN_TEST,
        DEPLOY_PLAN, EXECUTE_DEPLOY, CHAT_REPLY, QUICK_TASK,
    ]
}
