# 部署指南

## Docker 部署 (推荐)

```bash
# 构建
docker compose -f docker/docker-compose.yml build

# 启动 (Agent + Redis)
docker compose -f docker/docker-compose.yml up -d

# 查看日志
docker compose -f docker/docker-compose.yml logs -f agent
```

## 脚本部署

```bash
# 安装
bash deploy/deploy.sh install

# 启动
bash deploy/deploy.sh start

# 状态
bash deploy/deploy.sh status

# 更新
bash deploy/deploy.sh update

# 安装为 systemd 服务
bash deploy/deploy.sh service
```

## 手动部署

> 需要 Python 3.11+

```bash
# 1. 克隆代码
git clone https://github.com/allinxjd/xjd-agent.git /opt/xjd-agent
cd /opt/xjd-agent

# 2. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 3. 安装依赖 (基础版 / 全量版)
pip install -e .                # 基础: CLI + Web UI + 核心功能
pip install -e ".[all]"         # 全量: 包含所有消息平台 + 语音 + 浏览器

# 4. 引导式配置 (选择 Provider + 模型 + API Key)
xjd-agent setup

# 5. 启动
xjd-agent web --host 0.0.0.0 --port 8080
# 或
xjd-agent gateway --host 0.0.0.0 --port 18789
```

> 启动后也可以在 Web UI 的 Settings 面板中配置模型和 API Key。

## 消息平台配置

### Telegram

```yaml
channels:
  telegram:
    bot_token: "123456:ABCdef..."
```

### 微信 (企业微信)

```yaml
channels:
  wechat:
    app_id: "wx..."
    app_secret: "..."
    token: "..."
    encoding_aes_key: "..."
```

### Discord

```yaml
channels:
  discord:
    bot_token: "MTI..."
    command_prefix: "!"
```

### Slack

```yaml
channels:
  slack:
    bot_token: "xoxb-..."
    app_token: "xapp-..."
    signing_secret: "..."
```

### WhatsApp

```yaml
channels:
  whatsapp:
    access_token: "EAA..."
    phone_number_id: "..."
    verify_token: "xjd-agent"
    app_secret: "..."
```

### LINE

```yaml
channels:
  line:
    channel_secret: "..."
    channel_access_token: "..."
```

### Matrix

```yaml
channels:
  matrix:
    homeserver: "https://matrix.org"
    user_id: "@bot:matrix.org"
    password: "..."
    e2ee: false
```

## Redis 配置 (可选)

```yaml
redis:
  url: "redis://localhost:6379"
  db: 0
  key_prefix: "xjd:"
  pool_size: 10
```

功能: 消息队列 / 会话缓存 / 限流 / Pub/Sub 事件广播

## 监控

- **健康检查**: `GET /health`
- **Prometheus 指标**: `GET /metrics`
- 预定义指标: `xjd_requests_total`, `xjd_request_duration_seconds`, `xjd_tokens_total`, `xjd_errors_total`
