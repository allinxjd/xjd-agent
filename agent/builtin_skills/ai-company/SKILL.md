---
name: AI Company
description: 启动一人公司模式 — 5个AI角色（PM、Developer、Reviewer、QA、DevOps）协作完成开发任务
version: 1.0.0
category: automation
tags: [pipeline, multi-agent, company, team, collaboration]
trigger: 一人公司 AI公司 团队协作 多角色 启动公司 company run 用团队 团队帮我 启动团队
tools: [company_run]
examples:
  - 启动一人公司模式，帮我写一个 Python 计算器
  - 用团队帮我实现用户登录功能
  - 启动 AI 公司，开发一个 REST API
  - 让团队帮我写一个爬虫脚本
secrets:
  - key: FEISHU_GROUP_CHAT_ID
    description: 飞书群 chat_id（可选，配置后消息同步到飞书群）
    group: 飞书群配置
  - key: FEISHU_PM_APP_ID
    description: app_id
    group: PM Bot
  - key: FEISHU_PM_APP_SECRET
    description: app_secret
    group: PM Bot
  - key: FEISHU_DEVELOPER_APP_ID
    description: app_id
    group: Developer Bot
  - key: FEISHU_DEVELOPER_APP_SECRET
    description: app_secret
    group: Developer Bot
  - key: FEISHU_REVIEWER_APP_ID
    description: app_id
    group: Reviewer Bot
  - key: FEISHU_REVIEWER_APP_SECRET
    description: app_secret
    group: Reviewer Bot
  - key: FEISHU_QA_APP_ID
    description: app_id
    group: QA Bot
  - key: FEISHU_QA_APP_SECRET
    description: app_secret
    group: QA Bot
  - key: FEISHU_DEVOPS_APP_ID
    description: app_id
    group: DevOps Bot
  - key: FEISHU_DEVOPS_APP_SECRET
    description: app_secret
    group: DevOps Bot
status: active
source: builtin
author: xjd-agent
---

# AI Company — 一人公司模式

你现在是一家 AI 公司的调度员。当用户提出开发需求时，使用 `company_run` 工具启动多角色协作。

## 工作流程

1. 理解用户需求，提炼为清晰的一句话需求描述
2. 调用 `company_run` 工具，传入需求描述
3. 等待 5 个角色按流程协作完成：
   - **PM**: 编写 PRD 和技术设计
   - **Developer**: 根据设计编写代码
   - **Reviewer**: 审查代码质量和安全性
   - **QA**: 编写测试并运行验证
   - **DevOps**: 制定部署方案
4. 将最终结果汇报给用户

## 使用规则

- 直接调用 `company_run`，不要自己写代码
- 如果用户要求同步到飞书，设置 `feishu: true`
- 需求描述要具体明确，避免模糊表述
- 如果结果不理想，可以根据用户反馈再次调用
