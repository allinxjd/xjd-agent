"""电商垂直智能体模块 — 多 Agent 协作框架.

包含:
- roles: 电商 Agent 角色定义 (订单/库存/客服/营销)
- registry: Agent 注册与发现 (Redis-backed)
- task_queue: 分布式任务队列 (Redis Streams)
- coordinator: 电商协调器 (意图分类 + 多 Agent 委派)
- shared_memory: 共享 Agent 记忆
- tools: 电商工具接口 (Stub)
"""
