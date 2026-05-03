"""AI Company — 多 Agent 协作系统.

将 xjd-agent 打造为 AI 公司：多个 Agent 按角色分工（PM、开发、审查、测试、运维），
通过消息总线协作，可选绑定飞书机器人。

用法:
    from agent.company import Company
    from agent.company.roles import create_default_team

    company = Company(router=router, tool_registry=registry)
    for role in create_default_team():
        company.hire(role)
    result = await company.run("实现用户登录功能")
"""

from agent.company.chat_bridge import ChatBridge
from agent.company.company import Company, CompanyConfig, PipelineConfig, PipelineStage
from agent.company.environment import CompanyEnvironment
from agent.company.feishu_bridge import FeishuBotConfig, FeishuBridge
from agent.company.memory import CompanyMemory
from agent.company.message import CompanyMessage
from agent.company.role import CompanyRole
from agent.company.store import CompanyStore
from agent.company.task import CompanyTask

__all__ = [
    "ChatBridge",
    "Company",
    "CompanyConfig",
    "PipelineConfig",
    "PipelineStage",
    "CompanyEnvironment",
    "CompanyMemory",
    "CompanyMessage",
    "CompanyRole",
    "CompanyStore",
    "CompanyTask",
    "FeishuBotConfig",
    "FeishuBridge",
]
