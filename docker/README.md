# XJD Agent — Docker 部署指南

## 快速启动

```bash
# 1. 复制环境变量
cp docker/.env.example docker/.env
# 编辑 .env 填入 API Key

# 2. 启动 (Gateway 模式 — 默认)
cd docker && docker compose up -d

# 查看日志
docker compose logs -f agent
```

## 部署模式

### Gateway 模式 (默认)
WebSocket 网关，对接 Telegram/Discord/Slack/微信等消息渠道。

```bash
docker compose up -d
# 端口: 18789
```

### Web 模式
HTTP API + Web 聊天界面。

```bash
docker compose --profile web up -d
# 端口: 8080
```

### Chat 模式
交互式终端聊天。

```bash
docker compose --profile chat run --rm agent-chat
```

### 全部启动
```bash
docker compose --profile web --profile chat up -d
```

## 配置

环境变量通过 `docker/.env` 文件配置，参考 `.env.example`。

至少需要配置一个 AI Provider 的 API Key:
- `DEEPSEEK_API_KEY` — DeepSeek
- `OPENAI_API_KEY` — OpenAI
- `ANTHROPIC_API_KEY` — Anthropic Claude
- `GOOGLE_API_KEY` — Google Gemini

## 数据持久化

- Agent 数据: `agent_data` volume → `/data/.xjd-agent/`
- Redis 数据: `redis_data` volume → `/data/`

## 构建

```bash
# 从项目根目录构建
docker build -f docker/Dockerfile -t xjd-agent .

# 或用 compose
cd docker && docker compose build
```
