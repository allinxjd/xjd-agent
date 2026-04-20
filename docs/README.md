# 小巨蛋智能体 XJD Agent — 文档

面向商业落地的全能 AI Agent 平台 — 自我学习 · 多渠道网关 · 40+ 工具 · 生产级韧性

## 文档导航

| 文档 | 说明 |
|------|------|
| [架构设计](architecture.md) | 系统架构、消息流、模型路由、学习闭环、韧性设计 |
| [API 参考](api-reference.md) | REST API、WebSocket、SDK、认证 |
| [插件开发指南](plugin-guide.md) | 插件开发、MCP 协议、示例插件 |
| [部署指南](deployment.md) | Docker 部署、手动部署、消息平台配置 |

## 核心模块

| 模块 | 路径 | 说明 |
|------|------|------|
| Agent Engine | `agent/core/engine.py` | 核心对话循环与工具调用编排 |
| Model Router | `agent/core/model_router.py` | cheap/strong 分级路由与故障切换 |
| Credential Manager | `agent/core/credential_manager.py` | API Key 池轮转与自动恢复 |
| Terminal Manager | `agent/core/terminal.py` | 6 种终端后端统一管理 |
| Sandbox | `agent/core/sandbox.py` | 隔离执行环境 |
| Multi-Agent | `agent/core/multi_agent.py` | Sub-Agent 委派与协作 |
| Canvas | `agent/core/canvas.py` | A2UI 交互式 UI 生成 |
| Profile Manager | `agent/core/profile.py` | 多配置档管理 |
| Context References | `agent/core/context_references.py` | @file/@dir/@url/@symbol/@memory 上下文引用 |
| Agent Identity | `agent/core/identity.py` | 身份模板与人格配置 |
| PII Redactor | `agent/core/pii_redactor.py` | 敏感信息检测与脱敏 |
| Audit Logger | `agent/core/audit.py` | 持久化审计日志 (哈希链) |
| Cost Tracker | `agent/core/cost_tracker.py` | 模型调用成本统计与预算管理 |
| Hook Manager | `agent/core/hooks.py` | 事件钩子 + Webhook 入站 |
| RAG Pipeline | `agent/core/rag.py` | 检索增强生成管线 |
| Tool Registry | `agent/tools/registry.py` | 工具注册、查询、工具集组合 |
| Memory Manager | `agent/memory/manager.py` | 长期记忆与语义搜索 |
| Skill Manager | `agent/skills/manager.py` | 技能创建、存储、检索 |
| Learning Loop | `agent/skills/learning_loop.py` | 自动技能提取与策略优化 |
| Marketplace | `agent/skills/marketplace.py` | 社区技能市场 |
| Plugin Manager | `agent/plugins/manager.py` | 插件生命周期管理 |
| MCP Server | `agent/plugins/mcp_server.py` | IDE 集成 (JSON-RPC 2.0 stdio + SSE) |
| MCP Client | `agent/plugins/mcp_client.py` | 外部工具服务连接 |
| RL Trainer | `agent/training/rl_trainer.py` | 强化学习策略优化 |
| Gateway Server | `gateway/core/server.py` | WebSocket 消息网关 |
| Heartbeat | `gateway/core/heartbeat.py` | 定期健康检查 + 主动告警 |
| Voice Pipeline | `gateway/voice/pipeline.py` | STT + TTS 语音管线 |
| Wake Word | `gateway/voice/wake_word.py` | 唤醒词检测 (文本/音频) |
| Media Processor | `gateway/media/processor.py` | 图片生成 / 分析 / OCR |
| Canvas Renderer | `gateway/canvas/renderer.py` | A2UI 渲染引擎 |
| Cron Scheduler | `gateway/cron/scheduler.py` | 定时任务调度 |

## 快速链接

- [README](../README.md) — 项目概览与快速开始
- [CHANGELOG](../CHANGELOG.md) — 版本更新日志
- [CONTRIBUTING](../CONTRIBUTING.md) — 贡献指南
- [SECURITY](../SECURITY.md) — 安全策略
