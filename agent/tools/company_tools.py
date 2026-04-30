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

    return router, config


def _load_feishu_config(config):
    """从 config 加载飞书 Company bot 配置."""
    from agent.company.feishu_bridge import FeishuBotConfig

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
    from agent.company import Company
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

    company = Company(
        router=router,
        tool_registry=registry,
        feishu_chat_id=feishu_chat_id,
        feishu_bots=feishu_bots,
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


def register_company_tools(registry: Any) -> None:
    """注册 Company 工具到 ToolRegistry."""
    registry.register(
        name="company_run",
        description=(
            "启动 AI Company 多角色协作。5 个 AI 角色（PM、Developer、Reviewer、QA、DevOps）"
            "按流程协作完成开发任务：PM 写 PRD → Developer 写代码 → Reviewer 审查 → QA 测试 → DevOps 部署。"
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
    )
