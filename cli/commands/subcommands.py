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
  xjd-agent company run         AI 公司执行任务
  xjd-agent company interactive AI 公司交互模式
  xjd-agent company team        查看团队角色
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
    table.add_row("API Key", "***" + cfg.model.primary.api_key[-4:] if cfg.model.primary.api_key and len(cfg.model.primary.api_key) > 4 else "(未配置)")
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
    from agent.core.updater import (
        get_current_version, check_latest_version, compare_versions,
        auto_update, _git_repo_dir, _git_pending_commits,
    )

    current = get_current_version()
    console.print(f"  当前版本: [bold]{current}[/bold]")
    console.print("[dim]检查更新...[/dim]")

    try:
        latest = asyncio.run(check_latest_version())

        has_update = False
        if latest and latest.startswith("commit:"):
            parts = latest.split(":", 1)
            count = parts[1] if len(parts) > 1 else "?"
            console.print(f"  [yellow]发现 {count} 个新提交可更新[/yellow]")
            repo_dir = _git_repo_dir()
            if repo_dir:
                commits = _git_pending_commits(repo_dir)
                if commits:
                    for c in commits[:10]:
                        console.print(f"  [dim]  {c}[/dim]")
                    if len(commits) > 10:
                        console.print(f"  [dim]  ... 还有 {len(commits) - 10} 个[/dim]")
            has_update = True
        elif latest and compare_versions(current, latest):
            console.print(f"  [yellow]发现新版本: {latest}[/yellow]")
            has_update = True
        elif latest:
            console.print(f"  [green]已是最新版本 ({current})[/green]")
        else:
            console.print("  [yellow]无法检查更新 — 网络不通或代理未配置[/yellow]")
            console.print("  [dim]解决方法:[/dim]")
            console.print("  [dim]  1. 设置代理: export https_proxy=http://代理地址:端口[/dim]")
            console.print("  [dim]  2. 或在 ~/.xjd-agent/config.yaml 中添加: proxy: \"http://代理地址:端口\"[/dim]")
            console.print("  [dim]  3. 或手动更新: pip install --upgrade xjd-agent[/dim]")

        if has_update:
            if auto:
                console.print("  [dim]正在更新...[/dim]")
                ok = asyncio.run(auto_update())
                if ok:
                    console.print("  [green]更新成功![/green]")
                    from cli.commands.service import is_service_installed, restart_service
                    if is_service_installed():
                        if restart_service():
                            console.print("  [green]服务已自动重启[/green]")
                        else:
                            console.print("  [yellow]服务重启失败，请手动运行: xjd-agent gateway[/yellow]")
                    else:
                        console.print("  [yellow]如果 gateway 正在运行，请手动重启: Ctrl+C 后重新运行 xjd-agent gateway[/yellow]")
                else:
                    console.print("  [red]自动更新失败[/red]")
                    console.print("  [dim]手动更新: pip install --upgrade xjd-agent[/dim]")
            else:
                console.print("  运行 [bold]xjd-agent update --auto[/bold] 自动更新")

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
            (p.meta.description or "")[:60] or "-",
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

# ═══════════════════════════════════════════════════════════════════
#  company 子命令 (AI 公司)
# ═══════════════════════════════════════════════════════════════════

@click.group()
def company():
    """AI 公司 — 多 Agent 协作."""
    pass


def _build_company_router(config):
    """构建 ModelRouter，复用 chat 命令的初始化逻辑."""
    from agent.core.model_router import ModelRouter, build_credential_manager_from_config
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType

    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)

    primary = config.model.primary
    if not primary.provider or not primary.api_key:
        console.print("[red]未配置模型，请先运行 xjd-agent setup[/red]")
        return None

    provider = OpenAIProvider(
        provider_type=ProviderType(primary.provider),
        api_key=primary.api_key,
        base_url=primary.base_url or None,
    )
    router.register_provider(provider)
    router.set_primary(primary.provider, primary.model)

    if config.model.cheap:
        cheap = config.model.cheap
        if cheap.provider == primary.provider:
            router.set_cheap(cheap.provider, cheap.model)
        elif cheap.api_key:
            cheap_prov = OpenAIProvider(
                provider_type=ProviderType(cheap.provider),
                api_key=cheap.api_key,
                base_url=cheap.base_url or None,
            )
            router.register_provider(cheap_prov)
            router.set_cheap(cheap.provider, cheap.model)

    return router


def _load_feishu_config(config):
    """从 config.yaml 加载飞书配置."""
    from agent.company.feishu_bridge import FeishuBotConfig

    company_cfg = getattr(config, "company", None) or {}
    feishu_cfg = company_cfg.get("feishu", {}) if isinstance(company_cfg, dict) else {}
    chat_id = feishu_cfg.get("group_chat_id", "")
    roles_cfg = feishu_cfg.get("roles", {})

    if not chat_id or not roles_cfg:
        console.print("[yellow]飞书配置未找到，请在 config.yaml 中配置 company.feishu[/yellow]")
        return "", None

    bots = []
    for role_name, bot_cfg in roles_cfg.items():
        bots.append(FeishuBotConfig(
            app_id=bot_cfg.get("app_id", ""),
            app_secret=bot_cfg.get("app_secret", ""),
            role_name=role_name.upper() if len(role_name) <= 3 else role_name.capitalize(),
            verification_token=bot_cfg.get("verification_token", ""),
            encrypt_key=bot_cfg.get("encrypt_key", ""),
        ))
    return chat_id, bots


@company.command("run")
@click.argument("requirement")
@click.option("--max-rounds", "-r", default=20, help="最大轮次")
@click.option("--feishu", is_flag=True, help="启用飞书桥接")
def company_run(requirement: str, max_rounds: int, feishu: bool):
    """执行任务（自动编排）."""
    from agent.core.config import Config
    from agent.tools.registry import ToolRegistry
    from agent.tools.builtin import register_builtin_tools
    from agent.company import Company
    from agent.company.roles import create_default_team

    config = Config.load()
    config.apply_env_overrides()
    router = _build_company_router(config)
    if not router:
        return

    registry = ToolRegistry()
    register_builtin_tools(registry)

    feishu_chat_id, feishu_bots = "", None
    if feishu:
        feishu_chat_id, feishu_bots = _load_feishu_config(config)

    co = Company(
        router=router, tool_registry=registry,
        feishu_chat_id=feishu_chat_id, feishu_bots=feishu_bots,
    )
    co.hire_team(create_default_team())

    console.print(f"[bold cyan]AI 公司启动[/bold cyan] — 任务: {requirement[:80]}")
    feishu_tag = " | 飞书已连接" if feishu_bots else ""
    console.print(f"[dim]团队: PM, Developer, Reviewer, QA, DevOps | 最大轮次: {max_rounds}{feishu_tag}[/dim]\n")

    async def _run():
        if feishu_bots:
            await co.start_feishu()
        try:
            return await co.run(requirement, max_rounds=max_rounds)
        finally:
            await co.stop_feishu()

    result = asyncio.run(_run())
    if result:
        console.print(Panel(result[:2000], title="最终结果", border_style="green"))
    else:
        console.print("[yellow]任务未产出结果[/yellow]")


@company.command("interactive")
@click.argument("requirement", default="")
@click.option("--feishu", is_flag=True, help="启用飞书桥接")
def company_interactive(requirement: str, feishu: bool):
    """交互模式（可随时干预）."""
    from agent.core.config import Config
    from agent.tools.registry import ToolRegistry
    from agent.tools.builtin import register_builtin_tools
    from agent.company import Company
    from agent.company.roles import create_default_team

    config = Config.load()
    config.apply_env_overrides()
    router = _build_company_router(config)
    if not router:
        return

    registry = ToolRegistry()
    register_builtin_tools(registry)

    feishu_chat_id, feishu_bots = "", None
    if feishu:
        feishu_chat_id, feishu_bots = _load_feishu_config(config)

    co = Company(
        router=router, tool_registry=registry,
        feishu_chat_id=feishu_chat_id, feishu_bots=feishu_bots,
    )
    co.hire_team(create_default_team())

    if not requirement:
        requirement = click.prompt("请输入需求")

    console.print(f"[bold cyan]AI 公司 (交互模式)[/bold cyan] — {requirement[:80]}\n")

    async def _run():
        if feishu_bots:
            await co.start_feishu()
        try:
            return await co.run_interactive(requirement)
        finally:
            await co.stop_feishu()

    result = asyncio.run(_run())
    if result:
        console.print(Panel(result[:2000], title="最终结果", border_style="green"))


@company.command("team")
def company_team():
    """查看团队角色."""
    from agent.company.roles import create_default_team

    team = create_default_team()
    table = Table(title="AI 公司团队")
    table.add_column("角色", style="bold cyan")
    table.add_column("职责")
    table.add_column("监听", style="dim")
    table.add_column("动作", style="green")

    for role in team:
        table.add_row(
            role.name,
            role.description,
            ", ".join(role.watch_actions),
            ", ".join(a.name for a in role.actions),
        )

    console.print(table)


@company.command("status")
def company_status():
    """查看 AI 公司配置状态."""
    from agent.core.config import Config

    config = Config.load()
    company_cfg = getattr(config, "company", None) or {}
    feishu_cfg = company_cfg.get("feishu", {}) if isinstance(company_cfg, dict) else {}

    console.print("[bold]AI 公司配置状态[/bold]\n")

    from agent.company.company import _load_karpathy_guidelines
    guidelines = _load_karpathy_guidelines()
    if guidelines:
        console.print(f"  Karpathy 准则: [green]已加载[/green] ({len(guidelines)} chars)")
    else:
        console.print("  Karpathy 准则: [red]未找到[/red]")

    chat_id = feishu_cfg.get("group_chat_id", "")
    roles_cfg = feishu_cfg.get("roles", {})
    if chat_id:
        console.print(f"  飞书群: [green]{chat_id}[/green]")
        console.print(f"  飞书 Bot: {len(roles_cfg)} 个 ({', '.join(roles_cfg.keys())})")
    else:
        console.print("  飞书: [dim]未配置[/dim] (在 config.yaml 中添加 company.feishu)")

    from agent.company.roles import create_default_team
    team = create_default_team()
    console.print(f"  团队角色: {len(team)} 个 ({', '.join(r.name for r in team)})")

    from agent.company.yaml_loader import load_custom_roles, list_workflows
    custom = load_custom_roles()
    if custom:
        console.print(f"  自定义角色: {len(custom)} 个 ({', '.join(r.name for r in custom)})")
    workflows = list_workflows()
    if workflows:
        console.print(f"  工作流: {len(workflows)} 个")

    from agent.company.store import CompanyStore
    store = CompanyStore()
    store.open()
    resumable = store.get_resumable_run()
    if resumable:
        console.print(f"  [yellow]可续跑任务: {resumable['run_id']} — {resumable['requirement'][:50]}[/yellow]")
    store.close()


@company.command("history")
@click.option("--limit", "-n", default=10, help="显示条数")
def company_history(limit: int):
    """查看历史运行记录."""
    from agent.company.store import CompanyStore
    import time

    store = CompanyStore()
    store.open()
    runs = store.list_runs(limit=limit)
    store.close()

    if not runs:
        console.print("[dim]暂无运行记录[/dim]")
        return

    table = Table(title="运行历史")
    table.add_column("ID", style="dim")
    table.add_column("需求")
    table.add_column("状态")
    table.add_column("轮次", justify="right")
    table.add_column("时间")

    for r in runs:
        status_style = {"done": "green", "running": "cyan", "failed": "red"}.get(r["status"], "")
        started = time.strftime("%m-%d %H:%M", time.localtime(r["started_at"])) if r.get("started_at") else "-"
        table.add_row(
            r["run_id"],
            (r.get("requirement", "") or "")[:40],
            f"[{status_style}]{r['status']}[/{status_style}]" if status_style else r["status"],
            str(r.get("total_rounds", "-")),
            started,
        )
    console.print(table)


@company.command("init-role")
def company_init_role():
    """生成自定义角色 YAML 模板."""
    from agent.company.yaml_loader import create_example_role_yaml, ROLES_DIR

    ROLES_DIR.mkdir(parents=True, exist_ok=True)
    example_path = ROLES_DIR / "example-architect.yaml"
    if example_path.exists():
        console.print(f"[yellow]文件已存在: {example_path}[/yellow]")
        return
    example_path.write_text(create_example_role_yaml(), encoding="utf-8")
    console.print(f"[green]已生成角色模板: {example_path}[/green]")


@company.command("init-workflow")
def company_init_workflow():
    """生成自定义工作流 YAML 模板."""
    from agent.company.yaml_loader import create_example_workflow_yaml, WORKFLOWS_DIR

    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    example_path = WORKFLOWS_DIR / "example-quick-fix.yaml"
    if example_path.exists():
        console.print(f"[yellow]文件已存在: {example_path}[/yellow]")
        return
    example_path.write_text(create_example_workflow_yaml(), encoding="utf-8")
    console.print(f"[green]已生成工作流模板: {example_path}[/green]")
