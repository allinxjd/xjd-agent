"""CLI 主入口 — xjd-agent 命令.

子命令:
  xjd-agent          交互式对话 (默认)
  xjd-agent chat     交互式对话
  xjd-agent model    配置模型
  xjd-agent config   配置管理
  xjd-agent gateway  启动消息网关
  xjd-agent doctor   诊断检查
  xjd-agent setup    引导式配置
  xjd-agent update   更新到最新版本
"""

from __future__ import annotations

import asyncio
import logging
import sys

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

BANNER = """[bold cyan]
      ██╗  ██╗     ██╗██████╗
      ╚██╗██╔╝     ██║██╔══██╗
       ╚███╔╝      ██║██║  ██║
       ██╔██╗ ██   ██║██║  ██║
      ██╔╝ ██╗╚█████╔╝██████╔╝
      ╚═╝  ╚═╝ ╚════╝ ╚═════╝[/bold cyan]
[dim]    小 巨 蛋 智 能 体[/dim]
[dim italic]   Your Personal AI Agent[/dim italic]
"""

@click.group(invoke_without_command=True)
@click.pass_context
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
def cli(ctx: click.Context, verbose: bool) -> None:
    """小巨蛋智能体 — 你的个人 AI 助手."""
    log_level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # PII 脱敏 — 防止敏感信息泄漏到日志
    from agent.core.pii_redactor import PIIRedactor
    _pii = PIIRedactor()

    class _PIIFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            if isinstance(record.msg, str):
                record.msg = _pii.redact(record.msg)
            return True

    logging.getLogger().addFilter(_PIIFilter())

    if ctx.invoked_subcommand is None:
        # 默认进入交互式对话
        ctx.invoke(chat)

@cli.command()
@click.option("--model", "-m", default=None, help="指定模型 (provider:model)")
@click.option("--message", default=None, help="单次消息模式 (非交互)")
@click.option("--thinking", default=None, help="思考级别: off|low|medium|high")
@click.option("--allow-tools", "allow_tools", default=None, help="工具白名单 (逗号分隔)")
@click.option("--deny-tools", "deny_tools", default=None, help="工具黑名单 (逗号分隔)")
@click.option("--yolo", is_flag=True, help="跳过工具审批确认 (危险操作自动执行)")
@click.option("--worktree", "-w", is_flag=True, help="在 git worktree 中隔离运行")
@click.option("--session", "resume_session", default=None, help="恢复历史会话 (session ID)")
def chat(model: str | None, message: str | None, thinking: str | None, allow_tools: str | None, deny_tools: str | None, yolo: bool, worktree: bool, resume_session: str | None) -> None:
    """交互式对话."""
    asyncio.run(_chat_loop(model=model, single_message=message, thinking=thinking, allow_tools=allow_tools, deny_tools=deny_tools, yolo=yolo, worktree=worktree, resume_session=resume_session))

async def _chat_loop(
    model: str | None = None,
    single_message: str | None = None,
    thinking: str | None = None,
    allow_tools: str | None = None,
    deny_tools: str | None = None,
    yolo: bool = False,
    worktree: bool = False,
    resume_session: str | None = None,
) -> None:
    """交互式对话主循环."""
    from agent.core.config import Config
    from agent.core.engine import AgentEngine
    from agent.core.model_router import ModelRouter
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType
    from agent.tools.builtin import register_builtin_tools
    from agent.tools.extended import register_extended_tools
    from agent.tools.registry import ToolRegistry
    from agent.memory.manager import MemoryManager
    from agent.skills.manager import SkillManager
    from agent.skills.learning_loop import LearningLoop

    # 加载配置
    config = Config.load()
    config.apply_env_overrides()

    # 初始化 Router
    from agent.core.model_router import build_credential_manager_from_config
    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)

    # 注册 Provider
    primary = config.model.primary
    if not primary.provider or not primary.api_key:
        console.print(Panel(
            "[yellow]尚未配置 AI 模型。请运行:[/yellow]\n\n"
            "  [bold]xjd-agent setup[/bold]\n\n"
            "或设置环境变量:\n"
            "  export XJD_PRIMARY_PROVIDER=openai\n"
            "  export XJD_PRIMARY_MODEL=gpt-4o\n"
            "  export OPENAI_API_KEY=sk-...",
            title="⚙️  首次配置",
        ))
        return

    provider = OpenAIProvider(
        provider_type=ProviderType(primary.provider),
        api_key=primary.api_key,
        base_url=primary.base_url or None,
    )
    router.register_provider(provider)
    router.set_primary(primary.provider, primary.model)
    if config.model.failover:
        router.add_failover_from_config(config.model.failover)

    # cheap 路由
    if config.model.cheap:
        cheap = config.model.cheap
        if cheap.provider == primary.provider:
            router.set_cheap(cheap.provider, cheap.model)
        else:
            cheap_provider = OpenAIProvider(
                provider_type=ProviderType(cheap.provider),
                api_key=cheap.api_key,
                base_url=cheap.base_url or None,
            )
            router.register_provider(cheap_provider)
            router.set_cheap(cheap.provider, cheap.model)

    # 初始化学习系统
    memory_manager = MemoryManager()
    await memory_manager.initialize()

    skill_manager = SkillManager()
    await skill_manager.load_skills()

    learning_loop = LearningLoop(
        memory_manager=memory_manager,
        skill_manager=skill_manager,
    )

    # 注册工具
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    register_extended_tools(tool_registry)
    learning_loop._tool_registry = tool_registry

    # 注册技能管理工具 (Agent 可自主浏览/管理技能)
    from agent.tools.skill_tools import register_skill_tools
    register_skill_tools(tool_registry, skill_manager=skill_manager)

    # 注册记忆管理工具 (Agent 可自主读写记忆)
    from agent.tools.memory_tools import register_memory_tools
    register_memory_tools(tool_registry, memory_manager=memory_manager)

    # 注册知识画布工具 (记忆/学习/技能可视化)
    from agent.tools.knowledge_canvas import register_knowledge_canvas_tools
    register_knowledge_canvas_tools(tool_registry, memory_manager=memory_manager, learning_loop=learning_loop)

    if allow_tools:
        tool_registry.apply_allow_list([t.strip() for t in allow_tools.split(",")])
    if deny_tools:
        tool_registry.apply_deny_list([t.strip() for t in deny_tools.split(",")])

    # 初始化引擎 (集成学习系统 + 工具注册表)
    engine = AgentEngine(
        router=router,
        memory_manager=memory_manager,
        skill_manager=skill_manager,
        learning_loop=learning_loop,
        registry=tool_registry,
    )

    for tool in tool_registry.list_tools():
        engine.register_tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=tool.handler,
            requires_approval=False if yolo else tool.requires_approval,
        )

    # --worktree: 在 git worktree 中隔离运行
    worktree_dir = None
    if worktree:
        import subprocess
        import uuid
        try:
            branch = f"xjd-agent-{uuid.uuid4().hex[:6]}"
            worktree_dir = f"/tmp/xjd-worktree-{branch}"
            subprocess.run(["git", "worktree", "add", "-b", branch, worktree_dir], check=True, capture_output=True)
            import os
            os.chdir(worktree_dir)
            console.print(f"  [dim]Worktree: {worktree_dir} (branch: {branch})[/dim]")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            console.print(f"  [yellow]Worktree 创建失败: {e}[/yellow]")
            worktree_dir = None

    # 打印 Banner
    console.print(BANNER)
    flags = []
    if yolo:
        flags.append("[red]YOLO[/red]")
    if worktree_dir:
        flags.append("[cyan]WORKTREE[/cyan]")
    flags_str = f"  |  {'  '.join(flags)}" if flags else ""
    console.print(
        f"  模型: [bold]{primary.provider}:{primary.model}[/bold]  |  "
        f"工具: [bold]{len(tool_registry.list_tools())}[/bold] 个  |  "
        f"输入 /help 查看命令{flags_str}\n"
    )

    # 单次消息模式
    if single_message:
        result = await engine.run_turn(
            single_message,
            on_stream=lambda s: console.print(s, end=""),
            on_tool_call=lambda n, a: console.print(f"  🔧 {n}", style="dim"),
            thinking=thinking,
        )
        console.print()
        return

    # 交互式循环
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.completion import WordCompleter
    from agent.core.config import get_home

    # 斜杠命令自动补全
    slash_commands = WordCompleter(
        [
            "/exit", "/quit", "/q", "/new", "/reset", "/help",
            "/model", "/usage", "/skills", "/memory", "/learn",
            "/compact", "/rollback", "/sessions",
        ],
        sentence=True,
    )

    history_file = get_home() / "chat_history"
    session = PromptSession(history=FileHistory(str(history_file)), completer=slash_commands)

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: session.prompt("你> ")
            )

            if not user_input.strip():
                continue

            # 斜杠命令
            if user_input.startswith("/"):
                cmd = user_input.strip().lower()
                if cmd in ("/exit", "/quit", "/q"):
                    console.print("[dim]再见！👋[/dim]")
                    break
                elif cmd in ("/new", "/reset"):
                    engine.reset()
                    console.print("[dim]对话已重置。[/dim]")
                    continue
                elif cmd == "/help":
                    console.print(Panel(
                        "/new, /reset  — 重置对话\n"
                        "/model        — 显示当前模型\n"
                        "/usage        — 显示 token 用量\n"
                        "/skills       — 查看已学会的技能\n"
                        "/memory       — 查看记忆统计\n"
                        "/learn        — 查看学习统计\n"
                        "/compact      — 压缩对话历史 (节省 tokens)\n"
                        "/rollback     — 回滚到上一个检查点\n"
                        "/sessions     — 查看历史会话\n"
                        "/exit, /quit  — 退出",
                        title="📖 命令帮助",
                    ))
                    continue
                elif cmd == "/model":
                    console.print(f"  当前模型: [bold]{primary.provider}:{primary.model}[/bold]")
                    continue
                elif cmd == "/usage":
                    console.print(f"  对话轮次: {engine._turn_count}  |  消息数: {len(engine.messages)}")
                    continue
                elif cmd == "/skills":
                    skills = await skill_manager.list_skills()
                    if skills:
                        for s in skills:
                            console.print(f"  🎯 [bold]{s.name}[/bold] ({s.category}) — 使用 {s.use_count} 次")
                    else:
                        console.print("  [dim]暂无技能。完成任务后会自动学习。[/dim]")
                    continue
                elif cmd == "/memory":
                    stats = await memory_manager.get_stats()
                    console.print(f"  📝 记忆总数: {stats.get('total_memories', 0)}")
                    for mtype, count in stats.get('by_type', {}).items():
                        console.print(f"     {mtype}: {count}")
                    continue
                elif cmd == "/learn":
                    console.print(learning_loop.get_stats_summary())
                    continue
                elif cmd == "/compact":
                    from agent.context_engine.manager import ContextEngine
                    ctx_engine = ContextEngine()
                    compacted, stats = await ctx_engine.compact(engine.messages, router)
                    engine.messages = compacted
                    console.print(
                        f"  [dim]压缩完成: {stats['before_messages']}→{stats['after_messages']} 条消息, "
                        f"节省 {stats['saved_tokens']} tokens ({stats['compression_ratio']:.0%})[/dim]"
                    )
                    continue
                elif cmd == "/rollback":
                    from agent.core.checkpoint import CheckpointManager
                    cp_mgr = CheckpointManager()
                    cp_mgr.initialize()
                    cp = cp_mgr.rollback()
                    if cp:
                        console.print(f"  [dim]已回滚到检查点 {cp.checkpoint_id} ({cp.file_count} 文件)[/dim]")
                    else:
                        console.print("  [dim]没有可用的检查点。[/dim]")
                    continue
                elif cmd == "/sessions":
                    from gateway.core.session import SessionManager
                    sm = SessionManager()
                    sessions = await sm.list_sessions(active_only=False, limit=10)
                    if sessions:
                        for s in sessions:
                            status = "🟢" if s["is_active"] else "⚪"
                            console.print(f"  {status} {s['session_id'][:8]}  {s['message_count']} 条消息  {s['platform']}")
                    else:
                        console.print("  [dim]暂无历史会话。[/dim]")
                    continue

            # 调用 Agent
            console.print()
            result = await engine.run_turn(
                user_input,
                on_stream=lambda s: console.print(s, end=""),
                on_thinking=lambda s: console.print(f"[dim italic]{s[:200]}...[/dim italic]") if len(s) > 200 else None,
                on_tool_call=lambda n, a: console.print(f"\n  🔧 [bold]{n}[/bold]", style="cyan"),
                on_tool_result=lambda n, r: console.print(f"  ✅ {n}: {r[:100]}{'...' if len(r) > 100 else ''}", style="dim"),
                thinking=thinking,
            )
            console.print()

            # 显示用量
            if result.tool_calls_made > 0:
                console.print(
                    f"  [dim]工具调用: {result.tool_calls_made} 次  |  "
                    f"耗时: {result.duration_ms:.0f}ms  |  "
                    f"tokens: {result.total_usage.total_tokens}[/dim]"
                )
                console.print()

        except KeyboardInterrupt:
            console.print("\n[dim]按 Ctrl+C 再次退出，或输入 /exit[/dim]")
            continue
        except EOFError:
            break

@cli.command()
def setup() -> None:
    """引导式配置 (首次使用)."""
    from cli.commands.subcommands import setup_wizard
    setup_wizard()

@cli.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8080, help="监听端口")
def gateway(host: str, port: int) -> None:
    """启动消息网关 (WebUI + 消息渠道)."""
    asyncio.run(_start_web(host=host, port=port))

async def _start_gateway(host: str, port: int) -> None:
    """启动 Gateway."""
    from agent.core.config import Config
    from agent.core.engine import AgentEngine
    from agent.core.model_router import ModelRouter
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType
    from agent.tools.builtin import register_builtin_tools
    from agent.tools.registry import ToolRegistry
    from agent.tools.extended import register_extended_tools
    from agent.skills.manager import SkillManager
    from gateway.core.server import GatewayServer

    config = Config.load()
    config.apply_env_overrides()

    # 初始化 Router
    from agent.core.model_router import build_credential_manager_from_config
    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)
    primary = config.model.primary
    if primary.provider and primary.api_key:
        provider = OpenAIProvider(
            provider_type=ProviderType(primary.provider),
            api_key=primary.api_key,
            base_url=primary.base_url or None,
        )
        router.register_provider(provider)
        router.set_primary(primary.provider, primary.model)
        if config.model.failover:
            router.add_failover_from_config(config.model.failover)

    # 初始化 SkillManager + LearningLoop
    skill_manager = SkillManager()
    await skill_manager.load_skills()

    memory_manager = None
    try:
        from agent.memory.manager import MemoryManager
        memory_manager = MemoryManager()
        await memory_manager.initialize()
    except Exception:
        pass

    pin_manager = None
    try:
        from agent.context.pin_manager import ContextPinManager
        pin_manager = ContextPinManager(workspace_dir=os.getcwd())
        await pin_manager.initialize()
    except Exception:
        pass

    learning_loop = None
    try:
        from agent.skills.learning_loop import LearningLoop
        learning_loop = LearningLoop(
            memory_manager=memory_manager,
            skill_manager=skill_manager,
            pin_manager=pin_manager,
        )
    except Exception:
        pass

    # 注册工具
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    register_extended_tools(tool_registry)
    if learning_loop:
        learning_loop._tool_registry = tool_registry

    from agent.tools.skill_tools import register_skill_tools
    register_skill_tools(tool_registry, skill_manager=skill_manager)

    from agent.tools.memory_tools import register_memory_tools
    register_memory_tools(tool_registry, memory_manager=memory_manager)

    from agent.tools.knowledge_canvas import register_knowledge_canvas_tools
    register_knowledge_canvas_tools(tool_registry, memory_manager=memory_manager, learning_loop=learning_loop)

    # 注册 XjdHub 工具
    try:
        from agent.hub.client import XjdHubClient
        hub_client = XjdHubClient(skill_manager=skill_manager, hub_url=config.hub_url)
        await hub_client.initialize()
        from agent.tools.hub_tools import register_hub_tools
        register_hub_tools(tool_registry, hub_client=hub_client, skill_manager=skill_manager)
    except Exception:
        pass

    # 初始化引擎 (完整集成)
    engine = AgentEngine(
        router=router,
        memory_manager=memory_manager,
        skill_manager=skill_manager,
        learning_loop=learning_loop,
        registry=tool_registry,
    )

    for tool in tool_registry.list_tools():
        engine.register_tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=tool.handler,
            requires_approval=tool.requires_approval,
        )

    # 创建 Gateway
    gw = GatewayServer(
        agent_engine=engine,
        config={"host": host, "port": port, **config.channels},
    )

    # 根据配置注册平台适配器
    channels = config.channels
    if "telegram" in channels:
        try:
            from gateway.platforms.telegram import TelegramAdapter
            gw.register_adapter(TelegramAdapter(channels["telegram"]))
        except ImportError as e:
            console.print(f"  [yellow]Telegram: {e}[/yellow]")

    if "feishu" in channels:
        try:
            from gateway.platforms.feishu import FeishuAdapter
            gw.register_adapter(FeishuAdapter(channels["feishu"]))
        except ImportError as e:
            console.print(f"  [yellow]飞书: {e}[/yellow]")

    if "dingtalk" in channels:
        try:
            from gateway.platforms.dingtalk import DingTalkAdapter
            gw.register_adapter(DingTalkAdapter(channels["dingtalk"]))
        except ImportError as e:
            console.print(f"  [yellow]钉钉: {e}[/yellow]")

    if "wechat" in channels:
        try:
            from gateway.platforms.wechat import WeChatAdapter
            gw.register_adapter(WeChatAdapter(channels["wechat"]))
        except ImportError as e:
            console.print(f"  [yellow]微信: {e}[/yellow]")

    if "discord" in channels:
        try:
            from gateway.platforms.discord import DiscordAdapter
            gw.register_adapter(DiscordAdapter(channels["discord"]))
        except ImportError as e:
            console.print(f"  [yellow]Discord: {e}[/yellow]")

    if "slack" in channels:
        try:
            from gateway.platforms.slack import SlackAdapter
            gw.register_adapter(SlackAdapter(channels["slack"]))
        except ImportError as e:
            console.print(f"  [yellow]Slack: {e}[/yellow]")

    if "whatsapp" in channels:
        try:
            from gateway.platforms.whatsapp import WhatsAppAdapter
            gw.register_adapter(WhatsAppAdapter(channels["whatsapp"]))
        except ImportError as e:
            console.print(f"  [yellow]WhatsApp: {e}[/yellow]")

    if "line" in channels:
        try:
            from gateway.platforms.line import LineAdapter
            gw.register_adapter(LineAdapter(channels["line"]))
        except ImportError as e:
            console.print(f"  [yellow]LINE: {e}[/yellow]")

    if "matrix" in channels:
        try:
            from gateway.platforms.matrix import MatrixAdapter
            gw.register_adapter(MatrixAdapter(channels["matrix"]))
        except ImportError as e:
            console.print(f"  [yellow]Matrix: {e}[/yellow]")

    if "wechat_clawbot" in channels:
        try:
            from gateway.platforms.wechat_clawbot import WeChatClawBotAdapter
            gw.register_adapter(WeChatClawBotAdapter(channels["wechat_clawbot"]))
        except ImportError as e:
            console.print(f"  [yellow]微信 iLink: {e}[/yellow]")

    console.print(BANNER)
    console.print(Panel(
        f"  WebSocket: ws://{host}:{port}\n"
        f"  渠道: {len(gw._adapters)} 个已注册\n"
        f"  模型: {primary.provider}:{primary.model}",
        style="cyan",
    ))

    # 启动
    await gw.start()

    # 注册定时任务工具 (scheduler 在 gw.start() 中初始化)
    if gw._scheduler:
        from agent.tools.cron_tools import register_cron_tools
        register_cron_tools(tool_registry, gw._scheduler)
        for tool in tool_registry.list_tools():
            if tool.name == "scheduled_task":
                engine.register_tool(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    handler=tool.handler,
                )
                break

    # 用 signal handler 实现一次 Ctrl+C 干净退出
    import signal
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_sigint():
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, _on_sigint)
    loop.add_signal_handler(signal.SIGTERM, _on_sigint)

    await stop_event.wait()
    console.print("\n[dim]正在关闭网关...[/dim]")
    try:
        await asyncio.wait_for(gw.stop(), timeout=5)
    except asyncio.TimeoutError:
        console.print("[dim]关闭超时, 强制退出[/dim]")

    import os as _os
    _os._exit(0)

@cli.command()
def doctor() -> None:
    """诊断检查."""
    from agent.core.config import Config, get_home

    console.print(Panel("[bold]🩺 诊断检查[/bold]", style="cyan"))

    config = Config.load()
    config.apply_env_overrides()

    checks = [
        ("配置目录", str(get_home()), get_home().exists()),
        ("配置文件", str(get_home() / "config.yaml"), (get_home() / "config.yaml").exists()),
        ("Primary Provider", config.model.primary.provider or "(未配置)", bool(config.model.primary.provider)),
        ("Primary Model", config.model.primary.model or "(未配置)", bool(config.model.primary.model)),
        ("API Key", "***" + config.model.primary.api_key[-4:] if len(config.model.primary.api_key) > 4 else "(未配置)", bool(config.model.primary.api_key)),
    ]

    for name, value, ok in checks:
        icon = "✅" if ok else "❌"
        console.print(f"  {icon} {name}: {value}")

@cli.command()
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8080, help="监听端口")
def web(host: str, port: int) -> None:
    """启动 Web 聊天服务 (HTTP + WebSocket)."""
    asyncio.run(_start_web(host=host, port=port))

async def _start_web(host: str, port: int) -> None:
    """启动 Web 服务."""
    import os
    from agent.core.config import Config
    from agent.core.engine import AgentEngine
    from agent.core.model_router import ModelRouter
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType
    from agent.tools.builtin import register_builtin_tools
    from agent.tools.registry import ToolRegistry
    from web.server import WebServer

    config = Config.load()
    config.apply_env_overrides()

    # 初始化 Router + Engine
    from agent.core.model_router import build_credential_manager_from_config
    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)
    primary = config.model.primary
    if primary.provider and primary.api_key:
        provider = OpenAIProvider(
            provider_type=ProviderType(primary.provider),
            api_key=primary.api_key,
            base_url=primary.base_url or None,
        )
        router.register_provider(provider)
        router.set_primary(primary.provider, primary.model)
        if config.model.failover:
            router.add_failover_from_config(config.model.failover)

    # 初始化 SkillManager
    from agent.skills.manager import SkillManager
    skill_manager = SkillManager()
    await skill_manager.load_skills()

    # 初始化 MemoryManager
    memory_manager = None
    try:
        from agent.memory.manager import MemoryManager
        memory_manager = MemoryManager()
        await memory_manager.initialize()
    except Exception:
        pass  # MemoryManager optional in web mode

    # 注册工具
    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    from agent.tools.extended import register_extended_tools
    register_extended_tools(tool_registry)

    # 注册技能管理工具
    from agent.tools.skill_tools import register_skill_tools
    register_skill_tools(tool_registry, skill_manager=skill_manager)

    # 注册记忆管理工具
    from agent.tools.memory_tools import register_memory_tools
    register_memory_tools(tool_registry, memory_manager=memory_manager)

    # 注册 XjdHub 工具
    hub_client = None
    try:
        from agent.hub.client import XjdHubClient
        hub_client = XjdHubClient(skill_manager=skill_manager, hub_url=config.hub_url)
        await hub_client.initialize()
        from agent.tools.hub_tools import register_hub_tools
        register_hub_tools(tool_registry, hub_client=hub_client, skill_manager=skill_manager)
    except Exception:
        pass

    # 初始化 LearningLoop (连接 memory + skill + pin)
    from agent.skills.learning_loop import LearningLoop
    pin_manager = None
    try:
        from agent.context.pin_manager import ContextPinManager
        pin_manager = ContextPinManager(workspace_dir=os.getcwd())
        await pin_manager.initialize()
    except Exception:
        pass  # ContextPinManager optional in web mode

    learning_loop = LearningLoop(
        memory_manager=memory_manager,
        skill_manager=skill_manager,
        pin_manager=pin_manager,
        tool_registry=tool_registry,
    )

    # 注册知识画布工具 (需要 learning_loop)
    from agent.tools.knowledge_canvas import register_knowledge_canvas_tools
    register_knowledge_canvas_tools(tool_registry, memory_manager=memory_manager, learning_loop=learning_loop)

    # 初始化引擎 (传入 registry + 学习系统)
    engine = AgentEngine(
        router=router,
        memory_manager=memory_manager,
        skill_manager=skill_manager,
        learning_loop=learning_loop,
        registry=tool_registry,
        pin_manager=pin_manager,
    )
    engine._skill_manager = skill_manager
    engine._memory_manager = memory_manager

    for tool in tool_registry.list_tools():
        engine.register_tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=tool.handler,
            requires_approval=tool.requires_approval,
        )

    # 启动 Web 服务 (内嵌 Gateway)
    server = WebServer(agent_engine=engine)
    server._global_config = config
    server._pin_manager = pin_manager
    server._hub_client = hub_client

    # 内嵌 Gateway: 根据 channels 配置自动注册适配器
    if config.channels:
        from gateway.core.server import GatewayServer
        from gateway.platforms.schemas import ADAPTER_MAP
        import importlib

        gw = GatewayServer(
            agent_engine=engine,
            config={
                "host": config.gateway.host,
                "port": config.gateway.port,
                "voice": {"enabled": config.voice.enabled, "stt_provider": config.voice.stt_provider, "tts_provider": config.voice.tts_provider, "tts_voice": config.voice.tts_voice} if config.voice.enabled else None,
            },
            inspector_callback=server.broadcast_inspector_event,
        )

        for ch_name, ch_config in config.channels.items():
            entry = ADAPTER_MAP.get(ch_name)
            if not entry:
                continue
            try:
                mod = importlib.import_module(entry[0])
                adapter_cls = getattr(mod, entry[1])
                gw.register_adapter(adapter_cls(ch_config))
                console.print(f"  渠道: [bold]{ch_name}[/bold] 已注册")
            except (ImportError, AttributeError) as e:
                console.print(f"  [yellow]{ch_name}: {e}[/yellow]")

        server._gateway = gw
        gw._web_server = server
    else:
        # 创建空 Gateway (供 API 动态添加渠道)
        from gateway.core.server import GatewayServer
        gw = GatewayServer(agent_engine=engine, config={}, inspector_callback=server.broadcast_inspector_event)
        server._gateway = gw
        gw._web_server = server

    console.print(BANNER)
    console.print(Panel(
        f"  HTTP: http://{host}:{port}\n"
        f"  WebSocket: ws://{host}:{port}/ws\n"
        f"  模型: {primary.provider}:{primary.model}\n"
        f"  渠道: {len(config.channels)} 个已配置",
        style="cyan",
    ))

    await server.start(host=host, port=port)

    # 启动内嵌 Gateway
    if server._gateway:
        await server._gateway.start()

        # 注册定时任务工具 (scheduler 在 gw.start() 中初始化)
        if server._gateway._scheduler:
            from agent.tools.cron_tools import register_cron_tools
            register_cron_tools(tool_registry, server._gateway._scheduler)
            for tool in tool_registry.list_tools():
                if tool.name == "scheduled_task":
                    engine.register_tool(
                        name=tool.name,
                        description=tool.description,
                        parameters=tool.parameters,
                        handler=tool.handler,
                    )
                    break

        # 注册 Gateway 消息工具 (主动发消息给联系人)
        from agent.tools.gateway_tools import register_gateway_tools
        register_gateway_tools(tool_registry, server._gateway)
        for tool in tool_registry.list_tools():
            if tool.name in ("send_to_contact", "list_contacts", "set_contact_nickname"):
                engine.register_tool(
                    name=tool.name,
                    description=tool.description,
                    parameters=tool.parameters,
                    handler=tool.handler,
                )

    # 用 signal handler 实现一次 Ctrl+C 干净退出
    import signal
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_sigint():
        stop_event.set()

    loop.add_signal_handler(signal.SIGINT, _on_sigint)
    loop.add_signal_handler(signal.SIGTERM, _on_sigint)

    await stop_event.wait()
    console.print("\n[dim]正在关闭 Web 服务...[/dim]")

    async def _shutdown():
        if server._gateway:
            try:
                await server._gateway.stop()
            except Exception:
                pass
        await server.stop()
        # 关闭 pin_manager 确保 WAL 数据 flush
        try:
            await pin_manager.close()
        except Exception:
            pass

    try:
        await asyncio.wait_for(_shutdown(), timeout=5)
    except asyncio.TimeoutError:
        console.print("[dim]关闭超时, 强制退出[/dim]")

    # 飞书 SDK 会创建非 daemon 线程, threading._shutdown() 会卡住等它们 join
    # graceful shutdown 已完成, 直接退出进程
    import os as _os
    _os._exit(0)

@cli.command()
def version() -> None:
    """显示版本."""
    from agent.core.updater import get_current_version
    console.print(f"xjd-agent v{get_current_version()}")


@cli.command("serve-api")
@click.option("--host", default="0.0.0.0", help="监听地址")
@click.option("--port", default=8080, help="监听端口")
@click.option("--api-key", "api_key", default="", help="Bearer token 认证密钥 (空=不验证)")
def serve_api(host: str, port: int, api_key: str) -> None:
    """以 OpenAI 兼容 API 模式启动 (/v1/chat/completions)."""
    asyncio.run(_start_api(host=host, port=port, api_key=api_key))


async def _start_api(host: str, port: int, api_key: str) -> None:
    """启动 OpenAI 兼容 API."""
    from agent.core.config import Config
    from agent.core.engine import AgentEngine
    from agent.core.model_router import ModelRouter
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType
    from agent.tools.builtin import register_builtin_tools
    from agent.tools.registry import ToolRegistry
    from web.openai_api import OpenAIAPIServer, APIConfig

    config = Config.load()
    config.apply_env_overrides()

    from agent.core.model_router import build_credential_manager_from_config
    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)
    primary = config.model.primary
    if primary.provider and primary.api_key:
        provider = OpenAIProvider(
            provider_type=ProviderType(primary.provider),
            api_key=primary.api_key,
            base_url=primary.base_url or None,
        )
        router.register_provider(provider)
        router.set_primary(primary.provider, primary.model)
        if config.model.failover:
            router.add_failover_from_config(config.model.failover)

    engine = AgentEngine(router=router)

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry)
    for tool in tool_registry.list_tools():
        engine.register_tool(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            handler=tool.handler,
            requires_approval=tool.requires_approval,
        )

    api_config = APIConfig(host=host, port=port, api_key=api_key, model_name=f"{primary.provider}:{primary.model}")
    server = OpenAIAPIServer(agent_engine=engine, config=api_config)

    console.print(Panel(
        f"[bold]XJD 小巨蛋智能体 — OpenAI 兼容 API[/bold]\n\n"
        f"  Endpoint: http://{host}:{port}/v1/chat/completions\n"
        f"  Models:   http://{host}:{port}/v1/models\n"
        f"  认证: {'Bearer token' if api_key else '无 (开放访问)'}\n"
        f"  模型: {primary.provider}:{primary.model}",
        style="cyan",
    ))

    await server.start(host=host, port=port)

# 注册子命令组
try:
    from cli.commands.subcommands import model, config, plugin, skill, profile, identity
    cli.add_command(model)
    cli.add_command(config)
    cli.add_command(plugin)
    cli.add_command(skill)
    cli.add_command(profile)
    cli.add_command(identity)
except ImportError:
    pass

@cli.command()
@click.option("--auto", "auto", is_flag=True, help="自动更新")
def update(auto: bool) -> None:
    """检查更新."""
    from cli.commands.subcommands import check_update
    check_update(auto=auto)

@cli.command("serve-mcp")
@click.option("--transport", "-t", default="stdio", type=click.Choice(["stdio"]), help="传输方式")
def serve_mcp(transport: str) -> None:
    """以 MCP Server 模式启动，供 VS Code / Cursor 等 IDE 调用."""
    from agent.tools.registry import ToolRegistry
    from agent.tools.builtin import register_builtin_tools
    from agent.tools.extended import register_extended_tools
    from agent.plugins.mcp_server import MCPServer

    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_extended_tools(registry)

    server = MCPServer(tool_registry=registry)
    asyncio.run(server.start_stdio())


# ── Hub 技能市场命令组 ──

@cli.group()
def hub() -> None:
    """XjdHub 技能市场."""
    pass


@hub.command()
@click.argument("username")
@click.option("--password", "-p", prompt=True, hide_input=True, help="密码")
def login(username: str, password: str) -> None:
    """登录 Hub."""
    from agent.skills.marketplace import HubClient
    client = HubClient()
    try:
        token = asyncio.run(client.login(username, password))
        click.echo(f"登录成功 (token: {token[:20]}...)")
    except Exception as e:
        click.echo(f"登录失败: {e}", err=True)


@hub.command()
@click.argument("username")
@click.option("--email", "-e", required=True, help="邮箱")
@click.option("--password", "-p", prompt=True, hide_input=True, confirmation_prompt=True, help="密码")
def register(username: str, email: str, password: str) -> None:
    """注册 Hub 账号."""
    from agent.skills.marketplace import HubClient
    client = HubClient()
    try:
        asyncio.run(client.register(username, email, password))
        click.echo(f"注册成功: {username}")
    except Exception as e:
        click.echo(f"注册失败: {e}", err=True)


@hub.command()
@click.argument("query", default="")
@click.option("--category", "-c", default="", help="分类过滤")
def search(query: str, category: str) -> None:
    """搜索技能市场."""
    from agent.skills.marketplace import HubClient
    client = HubClient()
    results = asyncio.run(client.search(query=query, category=category))
    if not results:
        click.echo("未找到匹配的技能。")
        return
    for s in results:
        price = f"¥{s.price}" if s.price > 0 else "免费"
        click.echo(f"  {s.name} v{s.version} ({price}) ⬇{s.downloads}")
        click.echo(f"    {s.description}")
        if s.tags:
            click.echo(f"    标签: {', '.join(s.tags)}")


@hub.command()
@click.argument("slug")
def install(slug: str) -> None:
    """从 Hub 安装技能."""
    from agent.skills.marketplace import HubClient
    client = HubClient()
    result = asyncio.run(client.install(slug))
    if result.success:
        click.echo(f"安装成功: {slug}")
    else:
        click.echo(f"安装失败: {result.message}", err=True)


@hub.command()
@click.argument("path", type=click.Path(exists=True))
def publish(path: str) -> None:
    """发布技能到 Hub."""
    from agent.skills.marketplace import HubClient
    client = HubClient()
    result = asyncio.run(client.publish(path))
    if result.success:
        click.echo(f"发布成功: {result.slug} (待审核)")
    else:
        click.echo(f"发布失败: {result.message}", err=True)


@hub.command()
def keygen() -> None:
    """生成签名密钥对."""
    from hub.signing import SkillSigner
    signer = SkillSigner()
    pubkey = signer.generate_keys()
    click.echo(f"密钥对已生成。公钥: {pubkey}")
    click.echo("请将公钥上传到 Hub 个人资料 (hub login → update profile)。")


@hub.command()
def me() -> None:
    """查看当前登录信息."""
    from agent.skills.marketplace import HubClient
    client = HubClient()
    if not client._token:
        click.echo("未登录。请先运行: xjd-agent hub login")
        return
    click.echo(f"Hub: {client._hub_url}")
    click.echo(f"用户: {client._config.get('username', '未知')}")


@hub.command("serve")
@click.option("--host", "-h", default="0.0.0.0", help="监听地址")
@click.option("--port", "-p", default=8900, help="监听端口")
@click.option("--jwt-secret", default="", help="JWT 密钥")
def hub_serve(host: str, port: int, jwt_secret: str) -> None:
    """启动 Hub 服务端."""
    from hub.server import run_hub_server
    click.echo(f"XjdHub server starting on {host}:{port}")
    run_hub_server(host=host, port=port, jwt_secret=jwt_secret)


def main() -> None:
    """CLI 入口点."""
    cli()

if __name__ == "__main__":
    main()
