<p align="center">
<pre align="center">
██╗  ██╗     ██╗██████╗
╚██╗██╔╝     ██║██╔══██╗
 ╚███╔╝      ██║██║  ██║
 ██╔██╗ ██   ██║██║  ██║
██╔╝ ██╗╚█████╔╝██████╔╝
╚═╝  ╚═╝ ╚════╝ ╚═════╝
小 巨 蛋 智 能 体  v0.3.0
 Your Personal AI Agent
</pre>
</p>

<p align="center">
  A production-ready AI Agent platform with self-learning, multi-channel gateway, and 40+ tools.<br>
  面向商业落地的全能 AI Agent 平台 — 自我学习 · 多渠道网关 · 40+ 工具 · 生产级韧性
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/tests-511%20passed-brightgreen.svg" alt="Tests">
  <img src="https://img.shields.io/badge/providers-14-orange.svg" alt="Providers">
  <img src="https://img.shields.io/badge/platforms-20%2B-purple.svg" alt="Platforms">
</p>

---

## 特性亮点

| 能力 | 说明 |
|------|------|
| **14 种 AI Provider** | OpenAI / DeepSeek / Claude / Gemini / Qwen / Zhipu / 豆包 / Kimi / Groq / SiliconFlow 等统一接入 |
| **智能模型路由** | cheap/strong 自动分级，失败自动切换，凭证池轮转 (429 指数退避, 401 自动过期) |
| **20+ 消息平台** | 微信 / 飞书 / 钉钉 / Telegram / Discord / Slack / WhatsApp / LINE / Matrix / Teams / Signal / Email / SMS / iMessage / Twitter / Reddit / IRC / Facebook / Google Chat / Web |
| **40+ 工具** | 终端 / 文件 / Web 搜索 / 代码执行 / Git / 浏览器自动化 / Computer Use / 视觉分析 / Canvas |
| **自我学习闭环** | 评估 → 提取技能 → 优化策略 → 持久记忆，越用越强 |
| **强化学习训练** | 奖励模型 / 经验回放 / 策略优化 / 课程学习 / A/B 实验 |
| **语义向量搜索** | OpenAI / SentenceTransformers 嵌入 + SQLite 向量存储 |
| **6 种终端后端** | Local / SSH / Docker / Tmux / Daytona / Singularity |
| **多 Agent 协作** | Sub-Agent 委派，任务分解与并行执行 |
| **Canvas / A2UI** | Agent 生成交互式 UI: HTML / Markdown / Mermaid / Chart.js / React |
| **技能市场** | 社区技能搜索、安装、发布 + 70 个预置技能模板 |
| **MCP 协议** | Server (stdio + SSE 双传输) + Client (连接外部工具服务) |
| **成本追踪** | 按 provider/model 统计费用，预算告警，CSV 导出 |
| **事件钩子** | before/after 生命周期钩子 + Webhook 入站触发 |
| **心跳系统** | 定期健康检查 + 连续失败告警 + 主动通知 |
| **唤醒词** | 文本/音频唤醒词检测，支持 Porcupine / OpenWakeWord |
| **RAG 管线** | 文档加载 → 分块 → 嵌入 → 向量检索 → 上下文注入 |
| **智能家居** | Home Assistant REST API 集成 (设备控制/场景/自动化) |
| **反检测浏览器** | Stealth 模式: 隐藏 webdriver 标志 + 伪造指纹 |
| **插件系统** | 热加载 / 生命周期管理 / 3 个内置示例插件 |
| **语音管线** | Whisper STT + Edge TTS / ElevenLabs / OpenAI TTS |
| **生产级韧性** | 重试 / 断路器 / 令牌桶限流 / 舱壁隔离 / 降级 |
| **认证体系** | API Key / JWT / OAuth2 + RBAC 四级角色权限 |
| **监控告警** | Prometheus 指标 / 告警规则 / 分布式追踪 |
| **多配置档** | 独立的配置 / 记忆 / 技能环境，支持导入导出 |
| **上下文引用** | @file / @dir / @url / @symbol / @memory 内联引用，自动展开附加 |
| **身份模板** | 预置 4 种 Agent 人格 (助手/编程/研究/运维)，支持自定义 YAML |
| **PII 脱敏** | 手机号 / 身份证 / 邮箱 / 银行卡 / API Key / 密码自动检测与脱敏 |
| **审计日志** | 持久化 JSONL + SHA-256 哈希链，防篡改完整性校验 |
| **Inspector 监控** | 实时事件流面板 (LLM/Tool/Message/Cron/Error)，JSONL 审计持久化，历史查询 API |

## 快速开始

> 需要 Python 3.11+

```bash
# 克隆 & 安装
git clone https://github.com/allinxjd/xjd-agent.git && cd xjd-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 引导式配置 (选择 Provider + 模型 + API Key)
xjd-agent setup

# 交互对话
xjd-agent chat

# 启动 Web 聊天服务 (浏览器打开 http://localhost:8080)
xjd-agent web --port 8080

# 启动消息网关 (多平台接入)
xjd-agent gateway --port 18789

# 以 MCP Server 模式启动 (供 VS Code / Cursor 调用)
xjd-agent serve-mcp
```

> Web UI 启动后，也可以在 Settings 面板中直接配置模型 Provider 和 API Key，无需手动编辑配置文件。

## 环境变量

```bash
# 必需
export XJD_PRIMARY_PROVIDER=deepseek
export XJD_PRIMARY_MODEL=deepseek-chat
export DEEPSEEK_API_KEY=sk-xxx

# 可选: cheap 路由 (简单任务用便宜模型)
export XJD_CHEAP_PROVIDER=deepseek
export XJD_CHEAP_MODEL=deepseek-chat
```

## 项目结构

```
xjd-agent/
├── agent/                  # Agent 核心
│   ├── core/              # 引擎 / 配置 / 模型路由 / 沙箱 / 多Agent / 凭证轮转 / Canvas / 终端 / 配置档 / 身份模板 / PII脱敏 / 审计 / 上下文引用 / 成本追踪 / 事件钩子 / RAG
│   ├── providers/         # AI Provider 适配 (14 种)
│   ├── tools/             # 工具注册 + 内置工具 + 扩展工具 + Computer Use + 浏览器
│   ├── memory/            # 记忆管理 + 语义向量搜索
│   ├── skills/            # 技能管理 + 学习闭环 + 技能市场 + 优化器 + 评估器
│   ├── context_engine/    # 上下文窗口管理
│   ├── plugins/           # 插件系统 + MCP Server/Client
│   └── training/          # RL 训练 + 评估
├── gateway/               # 消息网关
│   ├── core/              # WebSocket 控制面 + Redis + 认证 + 监控 + 韧性
│   ├── platforms/         # 20+ 消息平台适配器
│   ├── voice/             # 语音管线 (STT + TTS)
│   ├── media/             # 媒体处理 (图片生成 / 分析 / OCR / 视频)
│   ├── canvas/            # Canvas A2UI 渲染
│   └── cron/              # 定时任务调度
├── web/                   # Web 前端 (HTTP + WebSocket 聊天 + Inspector 监控面板)
├── sdk/                   # Python SDK 客户端
├── cli/                   # CLI 命令行工具
├── skills/                # 70 个预置技能模板 (7 个分类)
├── tests/                 # 511 个测试
├── docs/                  # 文档
└── docker/                # Docker + Compose
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `xjd-agent` | 交互式对话 (默认) |
| `xjd-agent chat` | 交互式对话 |
| `xjd-agent web` | 启动 Web 聊天服务 |
| `xjd-agent gateway` | 启动消息网关 |
| `xjd-agent serve-mcp` | MCP Server 模式 (IDE 集成) |
| `xjd-agent setup` | 引导式配置向导 |
| `xjd-agent model list` | 列出支持的模型 |
| `xjd-agent model set` | 设置模型 |
| `xjd-agent model test` | 测试模型连通性 |
| `xjd-agent config show` | 显示当前配置 |
| `xjd-agent plugin list` | 列出插件 |
| `xjd-agent skill search` | 搜索技能市场 |
| `xjd-agent skill install` | 安装技能 |
| `xjd-agent profile list` | 列出配置档 |
| `xjd-agent profile create` | 创建配置档 |
| `xjd-agent identity list` | 列出身份模板 |
| `xjd-agent identity show` | 查看身份详情 |
| `xjd-agent identity create` | 创建自定义身份 |
| `xjd-agent identity use` | 切换身份模板 |
| `xjd-agent doctor` | 环境诊断 |
| `xjd-agent update` | 检查更新 |

## SDK 用法

```python
from sdk.client import XJDClient

client = XJDClient(base_url="http://localhost:8080")

# 聊天
response = await client.chat("分析这段代码的性能瓶颈")
print(response.content)

# 流式聊天
async for chunk in client.chat_stream("写一首关于编程的诗"):
    print(chunk, end="")

# 工具调用
result = await client.execute_tool("web_search", {"query": "Python 3.13 新特性"})

# 记忆
await client.memory.add("用户偏好 Python 和 TypeScript")
results = await client.memory.search("编程语言偏好")
```

## Docker 部署

```bash
# 构建
docker compose -f docker/docker-compose.yml build

# 启动 (Agent + Redis)
docker compose -f docker/docker-compose.yml up -d

# 查看日志
docker compose -f docker/docker-compose.yml logs -f agent
```

## 开发

```bash
# 安装开发依赖
make dev

# 运行测试
make test

# 代码检查
make lint

# 类型检查
make typecheck

# 格式化
make format
```

## 文档

- [架构设计](docs/architecture.md)
- [API 参考](docs/api-reference.md)
- [插件开发指南](docs/plugin-guide.md)
- [部署指南](docs/deployment.md)
- [更新日志](CHANGELOG.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

## License

[MIT](LICENSE)
