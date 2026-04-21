# XJD-Agent v0.3.1 Release Notes

**Release Date:** 2026-04-21

## Bug Fixes

### Cron 定时任务时区修复
- `croniter.get_next(float)` 对 naive datetime 返回 UTC epoch，在 CST (+8) 时区导致定时任务永不触发
- 改用 `get_next(datetime).timestamp()` 正确处理本地时区
- CronTask 新增 `skill_id` 字段，定时任务可绑定技能跳过运行时匹配

### Canvas 渲染推送修复
- `show_knowledge_canvas` 等间接调用 `_create_canvas` 的工具无法推送到前端
- 移除工具名白名单限制，改为检测返回值中的 `__canvas_render__` 标记

## New Features

### 工具作用域系统 (Scoped Tool Definitions)
三层工具过滤，在发送给模型之前裁剪工具列表，减少 token 消耗和工具误调用：
- **Layer 1 — 技能作用域**: 技能声明 `tools` 白名单时只发送这些工具
- **Layer 2 — 意图作用域**: 无技能时按用户消息关键词匹配相关工具集 (~8-12 个)
- **Layer 3 — 全量回退**: 意图模糊时发送 core + skills 全集 (21 个)

技能激活时工具数从 21 降至 ~3，token 节省 ~86%。

### 技能沙箱 (Skill Sandbox)
- 运行时拦截技能未声明的工具调用，防止模型越权
- cron 执行时自动排除 `scheduled_task` 工具，防止递归创建定时任务

### Canvas 持久化与导出
- **CanvasStore**: SQLite 持久化，支持 canvas 历史记录
- **CanvasExport**: 导出为 HTML / PDF / PNG 文件
- **知识画布**: 记忆网络图 (Mermaid)、学习曲线 (Chart.js)、技能树可视化

## Misc
- model_router: 改进 failover 日志
- providers/base: 新增 provider type
- cli/main.py: web 模式注册知识画布工具
- gateway/server: skill_id 透传到 engine.run_turn()

## Upgrade

```bash
# PyPI
pip install --upgrade xjd-agent

# Git
git pull && pip install -e .

# 内置命令
xjd-agent update --auto
```
