# 🚀 飞书群管理快速指南

## 📋 准备工作

### 1. 飞书开放平台配置
1. **访问** [飞书开放平台](https://open.feishu.cn/app)
2. **创建应用** - 选择"企业自建应用"
3. **开启机器人能力**
4. **获取以下信息**：
   - **App ID** - 应用唯一标识
   - **App Secret** - 应用密钥
   - **Verification Token** - 事件订阅验证令牌
   - **Encrypt Key** - 加密密钥（可选）

### 2. 事件订阅配置
1. 进入应用后台 → **事件订阅**
2. **请求地址**：`http://你的域名或IP:9001/feishu/webhook`
3. **添加事件**：
   - `im.message.receive_v1`（接收消息）
   - `im.chat.member.user.added_v1`（成员加入）
4. **添加权限**：
   - `im:message`（消息权限）
   - `im:message:send_as_bot`（以机器人身份发送消息）

## 🛠️ 部署步骤

### 方法一：使用部署脚本（推荐）

```bash
# 1. 给脚本执行权限
chmod +x deploy_feishu_bot.sh

# 2. 完整部署
./deploy_feishu_bot.sh all

# 或分步部署
./deploy_feishu_bot.sh install    # 安装依赖
./deploy_feishu_bot.sh configure  # 配置飞书
./deploy_feishu_bot.sh test       # 测试功能
./deploy_feishu_bot.sh start      # 启动机器人
```

### 方法二：手动部署

```bash
# 1. 安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install "xjd-agent[feishu]" pyyaml requests

# 2. 编辑配置文件
# 修改 config/feishu_config.yaml 中的飞书信息

# 3. 启动机器人
xjd-agent gateway --config config/feishu_config.yaml
```

## ⚙️ 配置文件说明

编辑 `config/feishu_config.yaml`：

```yaml
gateway:
  platforms:
    feishu:
      app_id: "你的App ID"           # ← 修改这里
      app_secret: "你的App Secret"    # ← 修改这里
      verification_token: "你的Verification Token"  # ← 修改这里
      mode: "webhook"  # 或 "long_poll"
      webhook_port: 9001
```

## 🌐 网络配置

### Webhook 模式（需要公网）
1. **确保服务器有公网 IP 或域名**
2. **开放防火墙端口**：9001
3. **配置反向代理**（可选）：
   ```nginx
   location /feishu/webhook {
       proxy_pass http://localhost:9001;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
   }
   ```

### 长连接模式（无需公网）
- 修改配置：`mode: "long_poll"`
- 无需公网 IP，适合内网环境

## 🤖 功能测试

### 1. 测试群管理功能
```bash
python3 skills/feishu_group_management.py
```

### 2. 测试飞书连接
1. 启动机器人后，在飞书群中 @机器人
2. 发送消息测试响应
3. 测试命令：帮助、统计、摘要等

## 📱 使用指南

### 基础命令
- **@机器人 + 问题** - 智能问答
- **帮助** - 查看完整功能列表
- **统计** - 查看群聊统计
- **摘要** - 生成每日摘要
- **提醒 时间 内容** - 设置提醒

### 群管理功能
1. **自动欢迎新成员**
2. **消息监控**（敏感词检测）
3. **定时提醒**（每日/每周）
4. **数据统计**（活跃度分析）
5. **智能问答**（常见问题自动回复）

## 🔧 自定义配置

### 修改欢迎消息
编辑 `skills/feishu_group_management.py` 中的 `welcome_new_member` 方法

### 添加监控关键词
修改配置文件：
```yaml
group_management:
  monitor_keywords: ["广告", "垃圾", "违规", "敏感词"]
```

### 添加自动回复规则
```yaml
auto_reply_rules:
  - trigger: "怎么安装"
    response: "请参考安装文档：https://example.com/docs"
  - trigger: "技术支持"
    response: "请联系管理员或发送邮件到 support@example.com"
```

## 🚨 故障排除

### 常见问题

#### 1. 机器人不响应
- ✅ 检查飞书开放平台事件订阅配置
- ✅ 验证 Verification Token 是否正确
- ✅ 检查服务器网络连接
- ✅ 查看日志文件：`logs/feishu_bot.log`

#### 2. Webhook 验证失败
- ✅ 确保公网 IP/域名可访问
- ✅ 检查防火墙端口 9001
- ✅ 验证 Verification Token 匹配

#### 3. 权限不足
- ✅ 检查飞书应用权限配置
- ✅ 确保添加了必要的权限范围

#### 4. 消息发送失败
- ✅ 检查 App Secret 是否正确
- ✅ 验证机器人是否已加入群聊
- ✅ 查看飞书开放平台错误信息

### 查看日志
```bash
# 实时查看日志
tail -f logs/feishu_bot.log

# 查看错误日志
grep -i error logs/feishu_bot.log
```

## 📈 高级功能

### 1. 数据库集成
启用数据库存储群数据：
```yaml
database:
  enabled: true
  type: "sqlite"
  path: "data/feishu_bot.db"
```

### 2. 自定义技能
创建新的技能文件：
```python
# skills/my_custom_skill.py
from xjd_agent.skills import Skill

class MyCustomSkill(Skill):
    name = "自定义技能"
    
    async def execute(self, context):
        # 你的业务逻辑
        return "执行完成"
```

### 3. 集成其他系统
- 连接数据库查询业务数据
- 调用外部 API 获取信息
- 集成 GitHub/GitLab 通知
- 连接监控系统发送告警

## 📞 支持与反馈

### 获取帮助
1. **查看文档**：运行 `帮助` 命令
2. **联系管理员**：在群内 @管理员
3. **查看日志**：定位具体问题

### 报告问题
1. 描述问题现象
2. 提供相关日志
3. 说明复现步骤
4. 期望的结果

### 功能建议
欢迎提出新功能建议，我们将持续优化！

---

**🎯 现在你可以开始部署你的飞书群管理机器人了！**

有任何问题，随时在飞书群中 @机器人 或联系技术支持。