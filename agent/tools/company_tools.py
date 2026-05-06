"""Company Tools — 让 chat 模式的 Agent 可以触发 AI Company 多角色协作."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _build_router():
    """构建 ModelRouter（复用 CLI 的初始化逻辑）."""
    from agent.core.config import Config
    from agent.core.model_router import ModelRouter, build_credential_manager_from_config
    from agent.providers.openai_provider import OpenAIProvider
    from agent.providers.base import ProviderType

    config = Config.load()
    config.apply_env_overrides()

    cred_mgr = build_credential_manager_from_config(config)
    router = ModelRouter(credential_manager=cred_mgr)

    primary = config.model.primary
    if not primary.provider or not primary.api_key:
        return None, config

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

    # 注册 configured_models 作为 failover
    for cm in getattr(config.model, 'configured_models', []):
        if cm.provider == primary.provider and cm.model == primary.model:
            continue
        if not cm.api_key:
            continue
        try:
            fo_prov = OpenAIProvider(
                provider_type=ProviderType(cm.provider),
                api_key=cm.api_key,
                base_url=cm.base_url or None,
            )
            router.register_provider(fo_prov)
            router.add_failover(cm.provider, cm.model)
        except Exception:
            pass

    return router, config


def _load_feishu_config(config):
    """从 skill secrets 或 config.yaml 加载飞书 Company bot 配置.

    优先读 skill secrets（WebUI 配置），fallback 到 config.yaml。
    """
    from agent.company.feishu_bridge import FeishuBotConfig

    # 优先从 skill secrets 读取
    try:
        from agent.core.secrets import get_secrets_store
        store = get_secrets_store()
        secrets = store.get_all("ai-company")
        if secrets.get("FEISHU_GROUP_CHAT_ID"):
            chat_id = secrets["FEISHU_GROUP_CHAT_ID"]
            role_keys = [
                ("PM", "FEISHU_PM_APP_ID", "FEISHU_PM_APP_SECRET"),
                ("Developer", "FEISHU_DEVELOPER_APP_ID", "FEISHU_DEVELOPER_APP_SECRET"),
                ("Reviewer", "FEISHU_REVIEWER_APP_ID", "FEISHU_REVIEWER_APP_SECRET"),
                ("QA", "FEISHU_QA_APP_ID", "FEISHU_QA_APP_SECRET"),
                ("DevOps", "FEISHU_DEVOPS_APP_ID", "FEISHU_DEVOPS_APP_SECRET"),
            ]
            bots = []
            for role_name, id_key, secret_key in role_keys:
                app_id = secrets.get(id_key, "")
                app_secret = secrets.get(secret_key, "")
                if app_id and app_secret:
                    bots.append(FeishuBotConfig(
                        app_id=app_id,
                        app_secret=app_secret,
                        role_name=role_name,
                    ))
            if bots:
                return chat_id, bots
    except Exception as e:
        logger.debug("Skill secrets load failed: %s", e)

    # Fallback: config.yaml
    company_cfg = getattr(config, "company", None) or {}
    feishu_cfg = company_cfg.get("feishu", {}) if isinstance(company_cfg, dict) else {}
    chat_id = feishu_cfg.get("group_chat_id", "")
    roles_cfg = feishu_cfg.get("roles", {})

    bots = []
    for role_name, role_cfg in roles_cfg.items():
        if role_cfg.get("app_id") and role_cfg.get("app_secret"):
            bots.append(FeishuBotConfig(
                app_id=role_cfg["app_id"],
                app_secret=role_cfg["app_secret"],
                role_name=role_name,
            ))
    return chat_id, bots


async def company_run(
    requirement: str,
    max_rounds: int = 20,
    feishu: bool = False,
) -> str:
    """启动 AI Company 多角色协作.

    5 个 AI 角色（PM、Developer、Reviewer、QA、DevOps）按流程协作完成任务。

    Args:
        requirement: 需求描述
        max_rounds: 最大协作轮次
        feishu: 是否同步到飞书群
    """
    from agent.tools.registry import ToolRegistry
    from agent.tools.builtin import register_builtin_tools
    from agent.company import Company, CompanyConfig
    from agent.company.roles import create_default_team

    router, config = _build_router()
    if not router:
        return "Error: 未配置模型，请先运行 xjd-agent setup"

    registry = ToolRegistry()
    register_builtin_tools(registry)

    feishu_chat_id = ""
    feishu_bots = None
    if feishu:
        feishu_chat_id, feishu_bots = _load_feishu_config(config)
        if not feishu_chat_id or not feishu_bots:
            return "Error: 飞书未配置，请在 config.yaml 中添加 company.feishu 段"

    company_cfg = CompanyConfig(boss_title=config.company_boss_title)
    company = Company(
        router=router,
        tool_registry=registry,
        feishu_chat_id=feishu_chat_id,
        feishu_bots=feishu_bots,
        config=company_cfg,
    )
    team = create_default_team()
    company.hire_team(team)

    if feishu:
        await company.start_feishu()

    try:
        result = await company.run(requirement, max_rounds=max_rounds)
    finally:
        if feishu:
            await company.stop_feishu()

    if not result:
        return "任务执行完成，但未产生输出。"

    if len(result) > 5000:
        return result[:5000] + "\n\n... (输出过长已截断)"
    return result


_standby_company: Any = None


async def company_standby(is_recovery: bool = False) -> str:
    """启动 AI Company 待命模式.

    各角色在飞书群报到并持续监听消息，等待用户指令。
    不 @人时 PM 回复，@某角色时该角色回复。
    用户下达开发任务时自动启动流水线。
    """
    import asyncio
    from agent.tools.registry import ToolRegistry
    from agent.tools.builtin import register_builtin_tools
    from agent.company import Company, CompanyConfig
    from agent.company.roles import create_default_team

    global _standby_company

    if _standby_company is not None:
        return "AI Company 待命模式已在运行中 🫡"

    router, config = _build_router()
    if not router:
        return "Error: 未配置模型，请先运行 xjd-agent setup"

    registry = ToolRegistry()
    register_builtin_tools(registry)

    feishu_chat_id, feishu_bots = _load_feishu_config(config)
    if not feishu_chat_id or not feishu_bots:
        return "Error: 飞书未配置，请在技能密钥页面配置飞书 Bot"

    company_cfg = CompanyConfig(boss_title=config.company_boss_title)
    company = Company(
        router=router,
        tool_registry=registry,
        feishu_chat_id=feishu_chat_id,
        feishu_bots=feishu_bots,
        config=company_cfg,
    )
    team = create_default_team()
    company.hire_team(team)

    _standby_company = company

    async def _run_standby():
        global _standby_company
        try:
            await company.run_standby(is_recovery=is_recovery)
        except Exception as e:
            logger.error("待命模式异常退出: %s", e)
        finally:
            _standby_company = None

    asyncio.create_task(_run_standby())

    for _ in range(30):
        await asyncio.sleep(1)
        if company._feishu_bridge and company._feishu_bridge._started:
            break

    # 持久化标志，gateway 重启后自动恢复
    try:
        from agent.core.config import Config as _Cfg
        _cfg = _Cfg.load()
        _cfg.company_standby_enabled = True
        _cfg.save()
    except Exception as e:
        logger.warning("保存 company_standby_enabled 失败: %s", e)

    bot_count = 0
    if company._feishu_bridge:
        bot_count = len(company._feishu_bridge._adapters)

    return (
        f"AI Company 待命模式已启动 🫡\n\n"
        f"飞书 Bot 已连接: {bot_count} 个\n"
        f"各角色已在飞书群报到，等待老板指令。\n\n"
        f"不 @人时 PM 回复，@某角色时该角色回复。\n"
        f"下达开发任务时自动启动流水线。"
    )


async def company_stop_standby() -> str:
    """停止 AI Company 待命模式."""
    global _standby_company
    if _standby_company is None:
        return "当前没有运行中的待命模式"
    _standby_company.stop_standby()
    _standby_company = None

    try:
        from agent.core.config import Config as _Cfg
        _cfg = _Cfg.load()
        _cfg.company_standby_enabled = False
        _cfg.save()
    except Exception as e:
        logger.warning("保存 company_standby_enabled 失败: %s", e)

    return "AI Company 待命模式已停止 👋"


def register_company_tools(registry: Any) -> None:
    """注册 Company 工具到 ToolRegistry."""
    registry.register(
        name="company_run",
        description=(
            "执行一次性开发任务。5 个 AI 角色（PM、Developer、Reviewer、QA、DevOps）"
            "按流水线协作完成：PM 写 PRD → Developer 写代码 → Reviewer 审查 → QA 测试 → DevOps 部署。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "requirement": {
                    "type": "string",
                    "description": "需求描述，如「写一个 Python 的 hello world 脚本」",
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "最大协作轮次 (默认 20)",
                    "default": 20,
                },
                "feishu": {
                    "type": "boolean",
                    "description": "是否同步消息到飞书群 (需要先配置飞书 bot)",
                    "default": False,
                },
            },
            "required": ["requirement"],
        },
        handler=company_run,
        category="company",
        timeout=600.0,
    )

    registry.register(
        name="company_standby",
        description=(
            "启动 AI Company 待命模式。各角色在飞书群报到并持续监听消息，等待用户指令。"
            "不 @人时 PM 回复，@某角色时该角色回复。用户下达开发任务时自动启动流水线。"
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=company_standby,
        category="company",
        timeout=60.0,
    )

    registry.register(
        name="company_stop_standby",
        description="停止 AI Company 待命模式，所有角色下线。",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=company_stop_standby,
        category="company",
        timeout=10.0,
    )
