# API 参考

## REST API

### 聊天

```
POST /api/chat
Content-Type: application/json
Authorization: Bearer <token>

{
    "message": "你好",
    "thinking": "medium"
}

Response:
{
    "content": "你好！有什么可以帮你的吗？",
    "tool_calls": 0,
    "tokens": 42,
    "duration_ms": 1200
}
```

### 认证状态

```
GET /api/auth/status

Response (无认证模式):
{"auth_enabled": false}

Response (首次运行，无用户):
{"auth_enabled": true, "needs_setup": true}

Response (已登录):
{"auth_enabled": true, "needs_setup": false, "user": {"user_id": "...", "username": "admin"}, "role": "admin"}

Response (未登录):
{"auth_enabled": true, "needs_setup": false}
```

### 登录

```
POST /api/auth/login
Content-Type: application/json

{"username": "admin", "password": "xxx"}

Response:
{"token": "eyJ...", "user_id": "...", "username": "admin", "role": "admin"}
```

### 注册

```
POST /api/auth/register
Content-Type: application/json
Authorization: Bearer <admin_token>

{"username": "newuser", "password": "xxx", "role": "user"}

首次运行 (无用户时) 不需要 admin token，第一个注册的用户自动成为 admin。
```

### 会话管理

```
GET /api/sessions     → 获取所有活跃会话
POST /api/reset       → 重置当前对话
```

### WebSocket 聊天

```javascript
const ws = new WebSocket('ws://localhost:8080/ws');

// 发送
ws.send(JSON.stringify({
    type: 'chat',
    message: '你好'
}));

// 接收事件
ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    // data.type: 'connected' | 'stream' | 'tool_call' | 'tool_result' | 'complete' | 'error'
};
```

### Admin API

```
GET /api/admin/stats      → 服务统计
GET /api/admin/models     → 模型配置
GET /api/admin/tools      → 工具列表
GET /api/admin/memory     → 记忆统计
GET /api/admin/sessions   → 会话列表
POST /api/admin/config    → 更新配置
```

### 健康检查

```
GET /health   → {"status": "ok", "uptime_seconds": 3600, ...}
GET /metrics  → Prometheus text format
```

### Inspector 监控

#### 历史事件查询

```
GET /api/admin/inspector/events?limit=100&type=tool_call
Authorization: Bearer <token>

参数:
  limit  — 返回条数上限 (默认 100, 最大 500)
  type   — 按事件类型过滤 (可选: llm_request, llm_response, tool_call, tool_result, message_in, message_out, cron_start, cron_complete, error)

Response:
{
    "events": [
        {
            "event_type": "tool_call",
            "title": "Tool Call",
            "detail": "web_search({\"query\": \"...\"})",
            "timestamp": 1713600000.123,
            "duration_ms": 450
        }
    ],
    "count": 1
}
```

#### WebSocket 订阅

```javascript
// 通过已有 WebSocket 连接订阅 Inspector 事件流
ws.send(JSON.stringify({ type: 'inspector_subscribe' }));

// 接收实时事件
ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'inspector_event') {
        // data.event_type: 'llm_request' | 'llm_response' | 'tool_call' | 'tool_result'
        //                 | 'message_in' | 'message_out' | 'cron_start' | 'cron_complete' | 'error'
        // data.title: 事件标题
        // data.detail: 事件详情
        // data.timestamp: Unix 时间戳
        // data.duration_ms: 耗时 (部分事件)
    }
};
```

## Python SDK

```python
from sdk.client import XJDClient

# HTTP 模式
client = XJDClient(base_url="http://localhost:8080", api_key="xjd_xxx")

# 内嵌模式 (无网络开销)
client = XJDClient.embedded()

# 聊天
response = await client.chat("Python GIL 是什么？")
print(response.content)
print(f"Tokens: {response.tokens}, Duration: {response.duration_ms}ms")

# 流式
async for chunk in client.chat_stream("写一首关于编程的诗"):
    print(chunk, end="")

# 工具
result = await client.execute_tool("web_search", {"query": "Python news"})

# 记忆
await client.memory.add("用户是 Python 开发者", memory_type="fact")
results = await client.memory.search("编程偏好")
memories = await client.memory.list(memory_type="fact")

# 管理
stats = await client.admin.stats()
health = await client.admin.health()
```

## 认证

### API Key

```bash
curl -H "Authorization: Bearer xjd_xxx" http://localhost:8080/api/chat
```

### JWT Token

```python
from gateway.core.auth import AuthManager

auth = AuthManager(secret_key="your-secret")
user = await auth.register_user("username", "password")
token = auth.create_token(user)
# 使用 token 在 Authorization header
```

### RBAC 权限

| 角色 | 权限 |
|------|------|
| admin | 全部 |
| user | chat, tools:use, memory:read/write |
| viewer | chat, memory:read |
| service | chat, tools:use, memory:read, admin:read |
