"""CLI 子命令 — model 配置 + config 管理 + update 更新 + setup 向导.

子命令:
  xjd-agent model list          列出可用模型
  xjd-agent model set           设置模型
  xjd-agent model test          测试模型连通性
  xjd-agent config show         显示配置
  xjd-agent config set          设置配置
  xjd-agent config path         显示配置路径
  xjd-agent setup               引导式配置向导
  xjd-agent update              检查更新
  xjd-agent plugin list         列出插件
  xjd-agent plugin enable       启用插件
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# ═══════════════════════════════════════════════════════════════════
#  model 子命令
# ═══════════════════════════════════════════════════════════════════

@click.group()
def model():
    """模型配置管理."""
    pass

@model.command("list")
@click.option("--provider", "-p", default=None, help="筛选 provider")
def model_list(provider: Optional[str]):
    """列出所有支持的模型."""
    from agent.providers.openai_provider import KNOWN_ENDPOINTS
    from agent.providers.anthropic_provider import CLAUDE_MODELS
    from agent.providers.google_provider import GEMINI_MODELS

    table = Table(title="支持的模型")
    table.add_column("Provider", style="cyan")
    table.add_column("Model", style="bold")
    table.add_column("Context", justify="right")
    table.add_column("Tier")
    table.add_column("Input $/MTok", justify="right")
    table.add_column("Output $/MTok", justify="right")

    # OpenAI-compatible
    if not provider or provider in ("openai", "deepseek", "siliconflow", "groq", "together"):
        for ep_name, ep in KNOWN_ENDPOINTS.items():
            for model_info in ep.get("models", {}).values():
                if isinstance(model_info, dict):
                    table.add_row(
                        ep_name,
                        model_info.get("id", ""),
                        str(model_info.get("context_length", "")),
                        model_info.get("tier", ""),
                        str(model_info.get("input_price", "")),
                        str(model_info.get("output_price", "")),
                    )

    # Anthropic
    if not provider or provider == "anthropic":
        for mid, info in CLAUDE_MODELS.items():
            table.add_row(
                "anthropic",
                info.model_id,
                f"{info.context_length:,}",
                info.tier,
                f"${info.input_price_per_mtok:.2f}",
                f"${info.output_price_per_mtok:.2f}",
            )

    # Google
    if not provider or provider == "google":
        for mid, info in GEMINI_MODELS.items():
            table.add_row(
                "google",
                info.model_id,
                f"{info.context_length:,}",
                info.tier,
                f"${info.input_price_per_mtok:.3f}",
                f"${info.output_price_per_mtok:.2f}",
            )

    console.print(table)

@model.command("set")
@click.argument("model_string")
def model_set(model_string: str):
    """设置主模型 (格式: provider:model, 如 openai:gpt-4o).

    例子:
      xjd-agent model set openai:gpt-4o
      xjd-agent model set deepseek:deepseek-chat
      xjd-agent model set anthropic:claude-sonnet-4-20250514
    """
    parts = model_string.split(":", 1)
    if len(parts) != 2:
        console.print("[red]格式错误。使用: provider:model (如 openai:gpt-4o)[/red]")
        return

    provider, model_name = parts

    from agent.core.config import Config, get_home
    config = Config.load()

    config.model.primary.provider = provider
    config.model.primary.model = model_name

    config.save()
    console.print(f"[green]已设置主模型: {provider}:{model_name}[/green]")

@model.command("test")
@click.option("--model", "-m", default=None, help="测试指定模型")
def model_test(model: Optional[str]):
    """测试模型连通性."""
    asyncio.run(_test_model(model))

async def _test_model(model_str: Optional[str]):
    from agent.core.config import Config
    from agent.core.model_router import ModelRouter
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType, Message

    config = Config.load()
    config.apply_env_overrides()
    primary = config.model.primary

    if not primary.api_key:
        console.print("[red]未配置 API Key[/red]")
        return

    provider = OpenAIProvider(
        provider_type=ProviderType(primary.provider),
        api_key=primary.api_key,
        base_url=primary.base_url or None,
    )

    test_model = model_str or primary.model
    console.print(f"[dim]Testing {primary.provider}:{test_model}...[/dim]")

    try:
        response = await provider.complete(
            messages=[Message(role="user", content="Say 'hello' in one word.")],
            model=test_model,
            temperature=0,
            max_tokens=10,
        )
        console.print(f"[green]✅ 成功！回复: {response.content}[/green]")
        console.print(f"[dim]Tokens: {response.usage.total_tokens}[/dim]")
    except Exception as e:
        console.print(f"[red]❌ 失败: {e}[/red]")

# ═══════════════════════════════════════════════════════════════════
#  config 子命令
# ═══════════════════════════════════════════════════════════════════

@click.group()
def config():
    """配置管理."""
    pass

@config.command("show")
@click.option("--raw", is_flag=True, help="显示原始 YAML")
def config_show(raw: bool):
    """显示当前配置."""
    from agent.core.config import Config, get_home

    config_path = get_home() / "config.yaml"

    if raw and config_path.exists():
        console.print(config_path.read_text(encoding="utf-8"))
        return

    cfg = Config.load()
    cfg.apply_env_overrides()

    table = Table(title="当前配置")
    table.add_column("Key", style="cyan")
    table.add_column("Value")

    table.add_row("配置目录", str(get_home()))
    table.add_row("Primary Provider", cfg.model.primary.provider or "(未配置)")
    table.add_row("Primary Model", cfg.model.primary.model or "(未配置)")
    table.add_row("API Key", "***" + cfg.model.primary.api_key[-4:] if len(cfg.model.primary.api_key) > 4 else "(未配置)")
    table.add_row("Base URL", cfg.model.primary.base_url or "(默认)")
    if cfg.model.cheap:
        table.add_row("Cheap Provider", cfg.model.cheap.provider)
        table.add_row("Cheap Model", cfg.model.cheap.model)

    console.print(table)

@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """设置配置项.

    例子:
      xjd-agent config set primary.provider openai
      xjd-agent config set primary.model gpt-4o
      xjd-agent config set primary.api_key sk-xxx
    """
    from agent.core.config import Config

    cfg = Config.load()

    key_parts = key.split(".")
    if len(key_parts) == 2 and key_parts[0] == "primary":
        setattr(cfg.model.primary, key_parts[1], value)
        cfg.save()
        console.print(f"[green]已设置 {key} = {value if 'key' not in key else '***'}[/green]")
    elif len(key_parts) == 2 and key_parts[0] == "cheap":
        if not cfg.model.cheap:
            from agent.core.config import ProviderConfig
            cfg.model.cheap = ProviderConfig()
        setattr(cfg.model.cheap, key_parts[1], value)
        cfg.save()
        console.print(f"[green]已设置 {key} = {value if 'key' not in key else '***'}[/green]")
    else:
        console.print(f"[red]未知配置项: {key}[/red]")
        console.print("[dim]支持: primary.provider, primary.model, primary.api_key, primary.base_url, cheap.*[/dim]")

@config.command("path")
def config_path():
    """显示配置文件路径."""
    from agent.core.config import get_home
    console.print(str(get_home() / "config.yaml"))

# ═══════════════════════════════════════════════════════════════════
#  setup 向导
# ═══════════════════════════════════════════════════════════════════

def setup_wizard():
    """引导式配置向导."""
    from agent.core.config import Config, get_home

    console.print(Panel("[bold]XJD 小巨蛋智能体 — 初始配置向导[/bold]", style="cyan"))

    config = Config.load()

    # 步骤 1: 选择 Provider
    console.print("\n[bold]第 1 步: 选择 AI 模型提供商[/bold]\n")
    providers = [
        ("1", "openai", "OpenAI (GPT-4o, o1)"),
        ("2", "deepseek", "DeepSeek (deepseek-chat, deepseek-reasoner)"),
        ("3", "anthropic", "Anthropic (Claude 4, Sonnet)"),
        ("4", "google", "Google (Gemini 2.0 Flash)"),
        ("5", "siliconflow", "SiliconFlow (硅基流动, 国内)"),
        ("6", "groq", "Groq (超快推理)"),
    ]

    for num, _, name in providers:
        console.print(f"  [{num}] {name}")

    choice = click.prompt("\n请选择", type=int, default=2)
    selected = providers[min(choice - 1, len(providers) - 1)]
    provider_name = selected[1]

    config.model.primary.provider = provider_name
    console.print(f"  → 选择: [bold]{selected[2]}[/bold]\n")

    # 步骤 2: 设置模型
    console.print("[bold]第 2 步: 选择模型[/bold]\n")

    model_suggestions = {
        "openai": ["gpt-4o", "gpt-4o-mini", "o1-mini"],
        "deepseek": ["deepseek-chat", "deepseek-reasoner"],
        "anthropic": ["claude-sonnet-4-20250514", "claude-3-5-haiku-20241022"],
        "google": ["gemini-2.0-flash", "gemini-1.5-pro"],
        "siliconflow": ["Qwen/Qwen2.5-72B-Instruct"],
        "groq": ["llama-3.3-70b-versatile"],
    }

    suggestions = model_suggestions.get(provider_name, ["default"])
    for i, s in enumerate(suggestions):
        console.print(f"  [{i + 1}] {s}")

    model_choice = click.prompt("\n请选择或输入模型名", default=suggestions[0])
    try:
        idx = int(model_choice) - 1
        if 0 <= idx < len(suggestions):
            model_choice = suggestions[idx]
    except ValueError:
        pass

    config.model.primary.model = model_choice
    console.print(f"  → 模型: [bold]{model_choice}[/bold]\n")

    # 步骤 3: API Key
    console.print("[bold]第 3 步: 设置 API Key[/bold]\n")
    api_key = click.prompt("  API Key", hide_input=True)
    config.model.primary.api_key = api_key
    console.print(f"  → Key: ***{api_key[-4:]}\n")

    # 步骤 4: Base URL (可选)
    base_urls = {
        "openai": "",
        "deepseek": "https://api.deepseek.com",
        "anthropic": "",
        "google": "",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "groq": "https://api.groq.com/openai/v1",
    }

    default_url = base_urls.get(provider_name, "")
    if default_url:
        config.model.primary.base_url = default_url
        console.print(f"  → Base URL: {default_url}\n")
    else:
        custom_url = click.prompt("  Base URL (回车跳过)", default="", show_default=False)
        if custom_url:
            config.model.primary.base_url = custom_url

    # 保存
    config.save()
    console.print(Panel(
        f"[green]✅ 配置完成！[/green]\n\n"
        f"  Provider: [bold]{provider_name}[/bold]\n"
        f"  Model: [bold]{model_choice}[/bold]\n"
        f"  配置文件: {get_home() / 'config.yaml'}\n\n"
        f"  运行 [bold]xjd-agent[/bold] 开始对话",
        title="XJD 配置成功",
        style="green",
    ))

# ═══════════════════════════════════════════════════════════════════
#  update 命令
# ═══════════════════════════════════════════════════════════════════

def check_update(auto: bool = False):
    """检查并更新到最新版本."""
    import asyncio
    from agent.core.updater import get_current_version, check_latest_version, compare_versions, auto_update

    current = get_current_version()
    console.print(f"  当前版本: [bold]{current}[/bold]")
    console.print("[dim]检查更新...[/dim]")

    try:
        latest = asyncio.get_event_loop().run_until_complete(check_latest_version())

        if latest and compare_versions(current, latest):
            console.print(f"  [yellow]发现新版本: {latest}[/yellow]")

            if auto:
                console.print("  [dim]正在自动更新...[/dim]")
                ok = asyncio.get_event_loop().run_until_complete(auto_update())
                if ok:
                    console.print("  [green]更新成功! 请重启 xjd-agent[/green]")
                else:
                    console.print("  [red]自动更新失败，请手动运行: pip install --upgrade xjd-agent[/red]")
            else:
                console.print("  运行 [bold]xjd-agent update --auto[/bold] 自动更新")
                console.print("  或手动: [bold]git pull && pip install -e .[/bold]")
        elif latest:
            console.print(f"  [green]已是最新版本 ({current})[/green]")
        else:
            console.print("  [yellow]无法检查远程版本，请手动运行: git pull[/yellow]")

        # 检查 git 状态 (fetch 已在 check_latest_version 中完成)
        repo_dir = str(__import__("pathlib").Path(__file__).parent.parent.parent)
        result = subprocess.run(
            ["git", "log", "--oneline", "HEAD..origin/main"],
            capture_output=True, text=True, timeout=10,
            cwd=repo_dir,
        )
        if result.returncode == 0 and result.stdout.strip():
            commits = result.stdout.strip().split("\n")
            console.print(f"\n  [yellow]Git 有 {len(commits)} 个新提交可拉取[/yellow]")
            console.print(f"  [dim]{result.stdout.strip()[:300]}[/dim]")
            console.print("  运行 [bold]xjd-agent update --auto[/bold] 或 [bold]git pull[/bold] 更新")

    except Exception as e:
        console.print(f"  [red]检查失败: {e}[/red]")

# ═══════════════════════════════════════════════════════════════════
#  plugin 子命令
# ═══════════════════════════════════════════════════════════════════

@click.group()
def plugin():
    """插件管理."""
    pass

@plugin.command("list")
def plugin_list():
    """列出所有插件."""
    asyncio.run(_plugin_list())

async def _plugin_list():
    from agent.plugins.manager import PluginManager

    pm = PluginManager()
    await pm.scan_plugins()

    plugins = pm.list_plugins()
    if not plugins:
        console.print("[dim]暂无已安装的插件。[/dim]")
        console.print("[dim]将插件放在 ~/.xjd-agent/plugins/ 目录即可发现。[/dim]")
        return

    table = Table(title="已发现的插件")
    table.add_column("名称", style="bold")
    table.add_column("版本")
    table.add_column("状态", style="cyan")
    table.add_column("描述")

    for p in plugins:
        table.add_row(
            p.name,
            p.meta.version,
            p.state.value,
            p.meta.description[:60] or "-",
        )

    console.print(table)

@plugin.command("enable")
@click.argument("name")
def plugin_enable(name: str):
    """启用插件."""
    asyncio.run(_plugin_enable(name))

async def _plugin_enable(name: str):
    from agent.plugins.manager import PluginManager

    pm = PluginManager()
    await pm.scan_plugins()

    ok = await pm.enable_plugin(name)
    if ok:
        console.print(f"[green]✅ 插件 {name} 已启用[/green]")
    else:
        console.print(f"[red]❌ 启用失败: {name}[/red]")

@plugin.command("disable")
@click.argument("name")
def plugin_disable(name: str):
    """禁用插件."""
    asyncio.run(_plugin_disable(name))

async def _plugin_disable(name: str):
    from agent.plugins.manager import PluginManager

    pm = PluginManager()
    await pm.scan_plugins()

    ok = await pm.disable_plugin(name)
    if ok:
        console.print(f"[green]✅ 插件 {name} 已禁用[/green]")
    else:
        console.print(f"[red]❌ 禁用失败: {name}[/red]")

# ═══════════════════════════════════════════════════════════════════
#  skill 子命令 (技能市场)
# ═══════════════════════════════════════════════════════════════════

@click.group()
def skill():
    """XjdHub 技能市场管理."""
    pass

@skill.command("search")
@click.argument("query", default="")
@click.option("--tag", "-t", default=None, help="按标签筛选")
def skill_search(query: str, tag: Optional[str]):
    """搜索技能市场."""
    asyncio.run(_skill_search(query, tag))

async def _skill_search(query: str, tag: Optional[str]):
    from agent.hub.client import XjdHubClient
    from agent.skills.manager import SkillManager

    sm = SkillManager()
    await sm.load_skills()
    hub = XjdHubClient(skill_manager=sm)
    await hub.initialize()

    results = await hub.search(query=query)
    if not results:
        console.print("[dim]未找到匹配的技能。[/dim]")
        return

    table = Table(title=f"XjdHub 技能搜索: {query or '全部'}")
    table.add_column("名称", style="bold")
    table.add_column("描述")
    table.add_column("作者")
    table.add_column("版本")
    table.add_column("价格")
    table.add_column("下载")

    for s in results:
        price = f"¥{s.price}" if s.price > 0 else "免费"
        table.add_row(s.name, s.description[:50], s.author, s.version, price, str(s.downloads))

    console.print(table)
    await hub.close()

@skill.command("install")
@click.argument("name")
def skill_install(name: str):
    """从 XjdHub 安装技能."""
    asyncio.run(_skill_install(name))

async def _skill_install(name: str):
    from agent.hub.client import XjdHubClient
    from agent.skills.manager import SkillManager

    sm = SkillManager()
    await sm.load_skills()
    hub = XjdHubClient(skill_manager=sm)
    await hub.initialize()

    result = await hub.install(name)
    if result.success:
        console.print(f"[green]技能 {name} 已安装 (ID: {result.skill_id})[/green]")
    else:
        console.print(f"[red]安装失败: {result.message}[/red]")
    await hub.close()

@skill.command("publish")
@click.argument("skill_id")
def skill_publish(skill_id: str):
    """发布技能到 XjdHub."""
    asyncio.run(_skill_publish(skill_id))

async def _skill_publish(skill_id: str):
    from agent.hub.client import XjdHubClient
    from agent.skills.manager import SkillManager

    sm = SkillManager()
    await sm.load_skills()
    hub = XjdHubClient(skill_manager=sm)
    await hub.initialize()

    result = await hub.publish(skill_id)
    if result.success:
        console.print(f"[green]技能已发布: {result.pkg_path}[/green]")
    else:
        console.print(f"[red]发布失败: {result.message}[/red]")
    await hub.close()

@skill.command("pack")
@click.argument("skill_id")
def skill_pack(skill_id: str):
    """打包技能为 .xjdpkg."""
    asyncio.run(_skill_pack(skill_id))

async def _skill_pack(skill_id: str):
    from agent.hub.client import XjdHubClient
    from agent.skills.manager import SkillManager

    sm = SkillManager()
    await sm.load_skills()
    hub = XjdHubClient(skill_manager=sm)

    try:
        pkg_path = await hub.pack(skill_id)
        console.print(f"[green]打包完成: {pkg_path}[/green]")
    except Exception as e:
        console.print(f"[red]打包失败: {e}[/red]")

@skill.command("unpack")
@click.argument("path")
def skill_unpack(path: str):
    """解包 .xjdpkg 安装技能."""
    asyncio.run(_skill_unpack(path))

async def _skill_unpack(path: str):
    from agent.hub.client import XjdHubClient
    from agent.skills.manager import SkillManager

    sm = SkillManager()
    await sm.load_skills()
    hub = XjdHubClient(skill_manager=sm)
    await hub.initialize()

    try:
        skill = await hub.unpack(path)
        console.print(f"[green]技能已安装: {skill.name} (ID: {skill.skill_id})[/green]")
    except Exception as e:
        console.print(f"[red]解包失败: {e}[/red]")
    await hub.close()

@skill.command("list")
def skill_list_installed():
    """列出本地已安装的技能."""
    asyncio.run(_skill_list())

async def _skill_list():
    from agent.skills.manager import SkillManager

    sm = SkillManager()
    await sm.load_skills()
    skills = await sm.list_skills()
    if not skills:
        console.print("[dim]暂无技能。[/dim]")
        return

    table = Table(title="本地技能")
    table.add_column("ID", style="bold")
    table.add_column("名称")
    table.add_column("状态")
    table.add_column("来源")
    table.add_column("版本")
    table.add_column("使用次数")

    for s in skills:
        status_color = {"active": "green", "draft": "yellow", "deprecated": "red"}.get(s.status, "dim")
        table.add_row(
            s.skill_id, s.name,
            f"[{status_color}]{s.status}[/{status_color}]",
            s.source, s.version, str(s.use_count),
        )

    console.print(table)

# ═══════════════════════════════════════════════════════════════════
#  profile 子命令 (多配置档)
# ═══════════════════════════════════════════════════════════════════

@click.group()
def profile():
    """多配置档管理."""
    pass

@profile.command("list")
def profile_list():
    """列出所有配置档."""
    from agent.core.profile import ProfileManager

    pm = ProfileManager()
    profiles = pm.list_profiles()

    table = Table(title="配置档")
    table.add_column("名称", style="bold")
    table.add_column("状态")
    table.add_column("描述")

    for p in profiles:
        status = "[green]● 活跃[/green]" if p.is_active else "[dim]○[/dim]"
        table.add_row(p.name, status, p.description or "-")

    console.print(table)

@profile.command("create")
@click.argument("name")
@click.option("--desc", "-d", default="", help="描述")
def profile_create(name: str, desc: str):
    """创建新配置档."""
    from agent.core.profile import ProfileManager

    pm = ProfileManager()
    if pm.create(name, desc):
        console.print(f"[green]✅ 配置档 {name} 已创建[/green]")
    else:
        console.print(f"[red]❌ 创建失败: {name} 已存在[/red]")

@profile.command("switch")
@click.argument("name")
def profile_switch(name: str):
    """切换配置档."""
    from agent.core.profile import ProfileManager

    pm = ProfileManager()
    if pm.switch(name):
        console.print(f"[green]已切换到: {name}[/green]")
    else:
        console.print(f"[red]配置档 {name} 不存在[/red]")

@profile.command("delete")
@click.argument("name")
def profile_delete(name: str):
    """删除配置档."""
    from agent.core.profile import ProfileManager

    pm = ProfileManager()
    if pm.delete(name):
        console.print(f"[green]✅ 配置档 {name} 已删除[/green]")
    else:
        console.print(f"[red]❌ 删除失败[/red]")

@profile.command("export")
@click.argument("name")
@click.argument("output_path")
def profile_export(name: str, output_path: str):
    """导出配置档."""
    from agent.core.profile import ProfileManager

    pm = ProfileManager()
    if pm.export_profile(name, output_path):
        console.print(f"[green]✅ 已导出到 {output_path}[/green]")
    else:
        console.print("[red]❌ 导出失败[/red]")

@profile.command("import")
@click.argument("archive_path")
@click.option("--name", "-n", default=None, help="导入后的名称")
def profile_import(archive_path: str, name: Optional[str]):
    """导入配置档."""
    from agent.core.profile import ProfileManager

    pm = ProfileManager()
    if pm.import_profile(archive_path, name):
        console.print("[green]✅ 导入成功[/green]")
    else:
        console.print("[red]❌ 导入失败[/red]")

# ═══════════════════════════════════════════════════════════════════
#  identity 子命令 (身份模板 / SOUL)
# ═══════════════════════════════════════════════════════════════════

@click.group()
def identity():
    """身份模板管理 (Agent 人格配置)."""
    pass

@identity.command("list")
def identity_list():
    """列出所有可用身份模板."""
    from agent.core.identity import AgentIdentity, BUILTIN_IDENTITIES
    from agent.core.config import get_home

    table = Table(title="身份模板")
    table.add_column("名称", style="bold")
    table.add_column("来源")
    table.add_column("角色")
    table.add_column("语言")

    # 内置
    for name, ident in BUILTIN_IDENTITIES.items():
        table.add_row(name, "[cyan]内置[/cyan]", ident.role[:30], ident.language)

    # 自定义
    identities_dir = get_home() / "identities"
    if identities_dir.exists():
        for name in AgentIdentity.list_available(identities_dir=identities_dir):
            loaded = AgentIdentity.load(name, identities_dir=identities_dir)
            table.add_row(name, "[green]自定义[/green]", loaded.role[:30], loaded.language)

    console.print(table)

@identity.command("show")
@click.argument("name")
def identity_show(name: str):
    """查看身份模板详情."""
    from agent.core.identity import AgentIdentity, BUILTIN_IDENTITIES
    from agent.core.config import get_home

    if name in BUILTIN_IDENTITIES:
        ident = BUILTIN_IDENTITIES[name]
    else:
        ident = AgentIdentity.load(name, identities_dir=get_home() / "identities")

    console.print(Panel(
        f"[bold]{ident.name}[/bold]\n\n"
        f"角色: {ident.role}\n"
        f"人格: {ident.personality}\n"
        f"语言: {ident.language}\n"
        f"语气: {ident.tone}\n"
        f"规则: {', '.join(ident.rules[:3]) if ident.rules else '无'}\n"
        f"能力: {', '.join(ident.capabilities[:3]) if ident.capabilities else '无'}\n"
        f"限制: {', '.join(ident.restrictions[:3]) if ident.restrictions else '无'}",
        title=f"🎭 {name}",
    ))

    console.print("\n[dim]System Prompt 预览:[/dim]")
    prompt = ident.to_system_prompt()
    console.print(prompt[:500] + ("..." if len(prompt) > 500 else ""))

@identity.command("create")
@click.argument("name")
@click.option("--role", "-r", prompt="角色描述", help="角色描述")
@click.option("--personality", "-p", default="", help="人格特征")
@click.option("--language", "-l", default="中文", help="语言")
def identity_create(name: str, role: str, personality: str, language: str):
    """创建自定义身份模板."""
    from agent.core.identity import AgentIdentity
    from agent.core.config import get_home

    identities_dir = get_home() / "identities"
    identities_dir.mkdir(parents=True, exist_ok=True)

    ident = AgentIdentity(
        name=name, role=role, personality=personality, language=language,
    )
    ident.save(identities_dir / f"{name}.yaml")
    console.print(f"[green]✅ 身份模板 {name} 已创建[/green]")

@identity.command("use")
@click.argument("name")
def identity_use(name: str):
    """切换当前使用的身份模板."""
    from agent.core.identity import AgentIdentity, BUILTIN_IDENTITIES
    from agent.core.config import Config, get_home

    # 验证存在
    if name not in BUILTIN_IDENTITIES:
        ident = AgentIdentity.load(name, identities_dir=get_home() / "identities")
        if ident.name == "XJD Agent" and name != "XJD Agent":
            console.print(f"[red]❌ 身份模板 {name} 不存在[/red]")
            return

    config = Config.load()
    config.identity = name
    config.save()
    console.print(f"[green]已切换身份: {name}[/green]")
