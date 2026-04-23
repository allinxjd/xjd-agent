"""电商垂直智能体模块 — 多 Agent 协作框架 + 店铺运营自动化.

包含:
- roles: 电商 Agent 角色定义 (订单/库存/客服/营销)
- registry: Agent 注册与发现 (Redis-backed)
- task_queue: 分布式任务队列 (Redis Streams)
- coordinator: 电商协调器 (意图分类 + 多 Agent 委派)
- shared_memory: 共享 Agent 记忆
- tools: 电商工具接口 (Stub)
- protocol: 标准化电商协议 (数据模型 + 错误码)
- base: 平台适配器抽象基类
- session: 浏览器会话管理 (多平台多账号)
- platforms/: 平台适配器 (pdd, taobao, jd, ...)
- operations/: 运营操作模块 (商品/订单/营销/分析/客服)
"""
