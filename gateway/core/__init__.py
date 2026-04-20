"""Gateway 核心 — WebSocket 控制面 + 会话管理 + Redis + 认证 + 监控 + 韧性.

组件:
- GatewayServer — 统一管理所有消息渠道
- SessionManager — 跨平台会话管理 + 持久化
- RedisManager — Redis 消息队列 + 会话缓存 + 限流
- AuthManager — API Key / JWT / OAuth2 认证 + RBAC
- MonitoringRegistry — Prometheus 指标 + 告警
- Resilience — 重试 / 断路器 / 限流 / 降级
"""
