# 架构设计

## 系统架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                     CLI / Web / SDK / MCP Server                     │
├──────────────────────────────────────────────────────────────────────┤
│                          Agent Engine                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐ │
│  │  Model   │ │  Tool    │ │  Memory  │ │ Learning │ │ Multi-    │ │
│  │  Router  │ │ Registry │ │  Manager │ │   Loop   │ │  Agent    │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └─────┬─────┘ │
│       │            │            │             │             │       │
│  ┌────┴─────┐ ┌────┴─────┐ ┌───┴──────┐ ┌───┴──────┐ ┌────┴─────┐ │
│  │Credential│ │ Sandbox  │ │ Semantic │ │   Skill  │ │  Canvas  │ │
│  │ Manager  │ │ Terminal │ │  Search  │ │Optimizer │ │  A2UI    │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
├──────────────────────────────────────────────────────────────────────┤
│                         Gateway Layer                                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ Provider │ │ Platform │ │  Voice   │ │  Media   │ │   Cron   │ │
│  │  (14种)  │ │ Adapter  │ │ Pipeline │ │Processor │ │Scheduler │ │
│  │          │ │  (20+)   │ │ STT+TTS  │ │ 图片/OCR │ │ 定时任务 │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐              │
│  │  Auth    │ │ Monitor  │ │Resilience│ │  Redis   │              │
│  │JWT/OAuth │ │Prometheus│ │断路器/限流│ │队列/缓存 │              │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘              │
├──────────────────────────────────────────────────────────────────────┤
│  Plugin System: MCP Client + MCP Server + Hot-loadable Plugins      │
│  Training: RL Trainer + Reward Model + Experience Replay + A/B Test │
└──────────────────────────────────────────────────────────────────────┘
```

## 消息流

```
用户消息 (微信/Telegram/Discord/Web/CLI/...)
    ↓
Platform Adapter → PlatformMessage (统一格式)
    ↓
Gateway Server (WebSocket 控制面)
    ↓
Auth Manager (认证 + 权限检查)
    ↓
Session Manager (跨平台会话)
    ↓
Agent Engine
    ├── Context Manager (窗口管理)
    ├── Memory Recall (记忆检索)
    ├── Skill Match (技能匹配)
    ├── Profile Manager (配置档切换)
    ↓
Model Router → Credential Manager → Provider (模型调用)
    ↓
Tool Calling Loop (最多 25 轮)
    ├── Tool Registry → Sandbox/Terminal 执行
    ├── 结果注入 → 模型再次调用
    └── 循环直到完成
    ↓
Response
    ├── Learning Loop (学习评估 → 技能提取 → 策略优化)
    ├── Memory Store (记忆持久化)
    ├── Canvas Update (A2UI 推送)
    └── Platform Adapter → 原始格式 → 用户
```

## 模型路由策略

```
incoming request
    ↓
classify(message) → cheap / strong
    ↓
┌─ cheap route ──→ DeepSeek Chat / GPT-4o-mini / Gemini Flash / ...
│
└─ strong route ─→ GPT-4o / Claude / DeepSeek Reasoner / ...
                       ↓ (429 rate limited)
                   Credential Manager → 切换 API Key (指数退避)
                       ↓ (全部 key 耗尽)
                   failover → next provider
                       ↓ (全部失败)
                   raise error
```

## 学习闭环

```
对话完成
    ↓
Evaluator (质量评估)
    ├── 规则评估 (完整性/相关性/安全性)
    └── LLM 评估 (cheap 模型)
    ↓
Reward Model (奖励计算)
    ↓
Experience Replay Buffer (经验存储)
    ↓
Policy Optimizer (策略优化)
    ├── System Prompt 微调
    ├── 工具偏好调整
    └── 回复风格优化
    ↓
Curriculum Scheduler (难度调整)
    ↓
Skill Extraction (技能提取)
    ↓
Auto Refinement (自动精炼)
    ├── use_count > 5 且 success_rate > 0.8 → 标记 "proven" (减少评估开销)
    ├── use_count > 3 且 success_rate < 0.5 → 自动触发 optimize
    └── Token 预算控制: get_skills_prompt(max_inject_tokens=2000)
    ↓
Memory Persistence (持久化)
```

## Session 隔离

```
Gateway 消息路由:
    Platform Adapter → PlatformMessage
        ↓
    Session Manager (per-user session)
        ↓
    Per-session Lock (串行处理同一用户消息)
        ↓
    构建 session_messages (从 session.messages[-20:])
        ↓
    engine.run_turn(message, session_messages=session_msgs)
        ↓
    Engine 使用传入的 session_messages 而非共享 self._messages
        ↓
    CLI 模式不受影响 (继续用 self._messages)

关键设计:
    - Gateway 不操作 engine.messages，避免并发状态污染
    - 每个 session 有独立的消息历史和锁
    - ModelRouter 的 provider 调用是无状态的，支持并发
    - 定期清理: 每 5 分钟清理闲置 session locks 和过期 sessions
```

## 终端后端

```
TerminalManager
    ├── LocalBackend      — asyncio.create_subprocess_shell
    ├── SSHBackend        — asyncssh 远程执行
    ├── DockerBackend     — docker exec 容器执行
    ├── TmuxBackend       — tmux send-keys + capture-pane 持久会话
    ├── DaytonaBackend    — daytona exec 开发环境
    └── SingularityBackend — singularity/apptainer exec HPC 容器
```

## Canvas / A2UI

```
Agent 生成内容
    ↓
CanvasManager.create(type, title, content)
    ↓
CanvasArtifact (HTML / Markdown / Mermaid / Chart.js / React)
    ↓
CanvasRenderer.render_html()
    ├── HTML → 直接渲染
    ├── Markdown → marked.js
    ├── Mermaid → mermaid.js CDN
    ├── Chart → chart.js CDN
    └── React → React + Babel CDN
    ↓
WebSocket push → 前端实时更新
```

## 多 Agent 协作

```
主 Agent (Coordinator)
    ├── 任务分解
    ├── Sub-Agent 委派
    │   ├── Agent A (代码分析)
    │   ├── Agent B (Web 搜索)
    │   └── Agent C (文档生成)
    ├── 结果汇总
    └── 最终回复
```

## 插件系统

```python
class MyPlugin(BasePlugin):
    async def on_enable(self):
        # 初始化

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "my_tool",
                "description": "...",
                "parameters": {...},
                "handler": self._handler,
            }
        ]

    async def _handler(self, **kwargs) -> str:
        return "result"
```

## MCP 协议

```
IDE (VS Code / Cursor)
    ↓ JSON-RPC 2.0 (stdio 或 SSE)
MCP Server (xjd-agent serve-mcp)
    ├── stdio 模式: 标准输入输出 (IDE 集成首选)
    ├── SSE 模式: HTTP /sse + POST /message (远程集成)
    ├── initialize → 协议握手
    ├── tools/list → 返回 ToolRegistry 中所有工具
    ├── tools/call → 执行工具并返回结果
    └── resources/list → 返回可用资源

xjd-agent
    ↓ JSON-RPC 2.0 (stdio/SSE)
MCP Client → 外部 MCP Server
    └── 自动注册为 "server::tool_name" 工具
```

## 韧性设计

| 组件 | 策略 |
|------|------|
| Provider 调用 | 指数退避重试 (3次) + 断路器 (3次连续失败 → 60s 冷却) + 凭证轮转 |
| 工具执行 | 超时 30s + 沙箱隔离 + 失败返回错误字符串 (不中断流程) |
| 消息队列 | Redis Streams Consumer Group + ACK 确认 |
| 限流 | 令牌桶 (API) + 滑动窗口 (Redis) |
| 并发控制 | Bulkhead 舱壁隔离 (限制最大并发数) |
| 降级 | fallback 装饰器 (异常时返回缓存/默认值) |
| WebSocket | Origin 验证 + 1MB 消息限制 + per-IP 10 连接上限 + 5s 发送超时 |
| 资源清理 | 定期清理 session locks / rate buckets / expired sessions |
| 记忆系统 | user_id 格式校验 (注入防护) + Memory API 100/min 限流 |
| 日志管道 | PII 脱敏 Filter (手机/身份证/邮箱/API Key 自动遮蔽) |
| 工具统计 | _tool_stats 超 10000 条自动淘汰旧数据 |
| 技能缓存 | 300s 过期自动重载，防止陈旧技能注入 |

## 认证体系

| 方式 | 说明 |
|------|------|
| API Key | `Authorization: Bearer xjd_xxx` (HMAC-SHA256 with server secret) |
| JWT | 用户名密码登录 → Token (2h 过期, PBKDF2-SHA256 600k 迭代) |
| OAuth2 | 第三方授权 |
| RBAC | admin / user / viewer / service 四级角色 |

首次运行流程:
1. `GET /api/auth/status` → `{needs_setup: true}`
2. 前端显示"创建管理员"引导
3. `POST /api/auth/register` (无需 admin token) → 第一个用户自动成为 admin
4. 后续注册需 admin token

## 安全与合规

```
用户输入
    ↓
PII Redactor (自动检测)
    ├── 手机号 → [PHONE]
    ├── 身份证 → [ID_CARD]
    ├── 邮箱 → [EMAIL]
    ├── 银行卡 → [BANK_CARD]
    ├── API Key → [API_KEY]
    └── 密码 → [PASSWORD]
    ↓
Agent Engine (处理脱敏后文本)
    ↓
Audit Logger (持久化审计)
    ├── JSONL 按日分文件
    ├── SHA-256 哈希链 (每条记录包含前一条哈希)
    └── verify_integrity() 完整性校验
```

## 上下文引用

```
用户输入: "看看 @file:main.py 和 @memory:编程偏好"
    ↓
ContextReferenceResolver.resolve()
    ├── @file:main.py → 读取文件内容
    ├── @dir:src → 列出目录
    ├── @url:https://... → 抓取网页
    ├── @symbol:ClassName → 搜索代码符号
    └── @memory:关键词 → 检索记忆
    ↓
展开后文本 + "--- Attached Context ---" 附加区块
```

## 身份模板

```
AgentIdentity (YAML 配置)
    ├── name / role / personality / language / tone
    ├── rules / capabilities / restrictions
    ├── greeting / custom_instructions
    ↓
to_system_prompt() → 注入 System Prompt
    ↓
预置身份:
    ├── assistant — 通用助手
    ├── coder — 编程专家
    ├── researcher — 研究分析
    └── ops — 运维专家
```