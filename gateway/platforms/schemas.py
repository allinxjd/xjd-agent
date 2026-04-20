"""平台配置字段 Schema — 前端动态渲染表单用.

每个平台定义所需的配置字段（key、类型、是否必填、默认值）。
前端通过 GET /api/admin/gateway/schemas 获取后动态生成表单。
"""

from __future__ import annotations

PLATFORM_SCHEMAS: dict[str, dict] = {
    "feishu": {
        "name": "飞书",
        "name_en": "Feishu/Lark",
        "icon": "feishu",
        "fields": [
            {"key": "app_id", "label": "App ID", "label_zh": "应用 ID", "type": "text", "required": True},
            {"key": "app_secret", "label": "App Secret", "label_zh": "应用密钥", "type": "password", "required": True},
            {"key": "mode", "label": "Mode", "label_zh": "接入模式", "type": "select", "required": True, "default": "long_poll",
             "options": [{"value": "long_poll", "label": "Long Poll (No public IP needed)", "label_zh": "长连接 (无需公网IP)"},
                         {"value": "webhook", "label": "Webhook (Requires public URL)", "label_zh": "Webhook (需要公网地址)"}]},
            {"key": "verification_token", "label": "Verification Token", "label_zh": "验证 Token", "type": "text", "required": False},
            {"key": "encrypt_key", "label": "Encrypt Key", "label_zh": "加密密钥", "type": "password", "required": False},
            {"key": "webhook_port", "label": "Webhook Port", "label_zh": "Webhook 端口", "type": "number", "default": 9001},
        ],
    },
    "telegram": {
        "name": "Telegram",
        "name_en": "Telegram",
        "icon": "telegram",
        "fields": [
            {"key": "bot_token", "label": "Bot Token", "label_zh": "Bot Token", "type": "password", "required": True},
            {"key": "webhook_url", "label": "Webhook URL", "label_zh": "Webhook 地址", "type": "text", "required": False},
        ],
    },
    "discord": {
        "name": "Discord",
        "name_en": "Discord",
        "icon": "discord",
        "fields": [
            {"key": "bot_token", "label": "Bot Token", "label_zh": "Bot Token", "type": "password", "required": True},
            {"key": "guild_id", "label": "Guild ID", "label_zh": "服务器 ID", "type": "text", "required": False},
        ],
    },
    "slack": {
        "name": "Slack",
        "name_en": "Slack",
        "icon": "slack",
        "fields": [
            {"key": "bot_token", "label": "Bot Token (xoxb-)", "label_zh": "Bot Token", "type": "password", "required": True},
            {"key": "app_token", "label": "App Token (xapp-)", "label_zh": "App Token", "type": "password", "required": True},
            {"key": "signing_secret", "label": "Signing Secret", "label_zh": "签名密钥", "type": "password", "required": False},
        ],
    },
    "dingtalk": {
        "name": "钉钉",
        "name_en": "DingTalk",
        "icon": "dingtalk",
        "fields": [
            {"key": "app_key", "label": "App Key", "label_zh": "App Key", "type": "text", "required": True},
            {"key": "app_secret", "label": "App Secret", "label_zh": "App Secret", "type": "password", "required": True},
            {"key": "robot_code", "label": "Robot Code", "label_zh": "机器人编码", "type": "text", "required": False},
        ],
    },
    "wechat": {
        "name": "企业微信",
        "name_en": "WeCom",
        "icon": "wechat",
        "fields": [
            {"key": "mode", "label": "Mode", "label_zh": "接入模式", "type": "select", "required": True, "default": "aibot",
             "options": [
                 {"value": "aibot", "label": "Smart Bot WebSocket (Recommended)", "label_zh": "智能机器人长连接 (推荐)"},
                 {"value": "work", "label": "Self-built App Webhook", "label_zh": "自建应用回调 (需公网IP)"},
                 {"value": "wechaty", "label": "Wechaty (Personal WeChat)", "label_zh": "Wechaty (个人微信)"},
             ]},
            {"key": "bot_id", "label": "Bot ID", "label_zh": "机器人 ID", "type": "text", "required": False,
             "show_when": {"mode": "aibot"}},
            {"key": "secret", "label": "Secret", "label_zh": "机器人 Secret", "type": "password", "required": False,
             "show_when": {"mode": "aibot"}},
            {"key": "corp_id", "label": "Corp ID", "label_zh": "企业 ID", "type": "text", "required": False,
             "show_when": {"mode": "work"}},
            {"key": "corp_secret", "label": "Corp Secret", "label_zh": "应用密钥", "type": "password", "required": False,
             "show_when": {"mode": "work"}},
            {"key": "agent_id", "label": "Agent ID", "label_zh": "应用 AgentId", "type": "text", "required": False,
             "show_when": {"mode": "work"}},
            {"key": "token", "label": "Token", "label_zh": "回调 Token", "type": "text", "required": False,
             "show_when": {"mode": "work"}},
            {"key": "encoding_aes_key", "label": "Encoding AES Key", "label_zh": "消息加密密钥", "type": "password", "required": False,
             "show_when": {"mode": "work"}},
            {"key": "webhook_port", "label": "Webhook Port", "label_zh": "Webhook 端口", "type": "number", "default": 9002,
             "show_when": {"mode": "work"}},
            {"key": "wechaty_token", "label": "Puppet Token", "label_zh": "Puppet Token", "type": "password", "required": False,
             "show_when": {"mode": "wechaty"}},
            {"key": "wechaty_endpoint", "label": "Puppet Endpoint", "label_zh": "Puppet 地址", "type": "text", "required": False,
             "show_when": {"mode": "wechaty"}},
        ],
    },
    "whatsapp": {
        "name": "WhatsApp",
        "name_en": "WhatsApp",
        "icon": "whatsapp",
        "fields": [
            {"key": "phone_number_id", "label": "Phone Number ID", "label_zh": "电话号码 ID", "type": "text", "required": True},
            {"key": "access_token", "label": "Access Token", "label_zh": "访问令牌", "type": "password", "required": True},
            {"key": "verify_token", "label": "Verify Token", "label_zh": "验证 Token", "type": "text", "required": True},
        ],
    },
    "line": {
        "name": "LINE",
        "name_en": "LINE",
        "icon": "line",
        "fields": [
            {"key": "channel_access_token", "label": "Channel Access Token", "label_zh": "频道访问令牌", "type": "password", "required": True},
            {"key": "channel_secret", "label": "Channel Secret", "label_zh": "频道密钥", "type": "password", "required": True},
        ],
    },
    "matrix": {
        "name": "Matrix",
        "name_en": "Matrix",
        "icon": "matrix",
        "fields": [
            {"key": "homeserver", "label": "Homeserver URL", "label_zh": "服务器地址", "type": "text", "required": True},
            {"key": "access_token", "label": "Access Token", "label_zh": "访问令牌", "type": "password", "required": True},
            {"key": "user_id", "label": "Bot User ID", "label_zh": "Bot 用户 ID", "type": "text", "required": True},
        ],
    },
}

# 适配器类映射 (平台名 → 模块路径, 类名)
ADAPTER_MAP: dict[str, tuple[str, str]] = {
    "feishu": ("gateway.platforms.feishu", "FeishuAdapter"),
    "telegram": ("gateway.platforms.telegram", "TelegramAdapter"),
    "discord": ("gateway.platforms.discord", "DiscordAdapter"),
    "slack": ("gateway.platforms.slack", "SlackAdapter"),
    "dingtalk": ("gateway.platforms.dingtalk", "DingTalkAdapter"),
    "wechat": ("gateway.platforms.wechat", "WeChatAdapter"),
    "whatsapp": ("gateway.platforms.whatsapp", "WhatsAppAdapter"),
    "line": ("gateway.platforms.line", "LineAdapter"),
    "matrix": ("gateway.platforms.matrix", "MatrixAdapter"),
}


def get_platform_names() -> list[str]:
    """获取所有支持的平台名."""
    return list(PLATFORM_SCHEMAS.keys())