"""消息平台适配器 — 支持 20 渠道接入.

核心适配器:
- Telegram — python-telegram-bot
- 飞书 — lark-oapi
- 钉钉 — dingtalk-stream
- 微信 — wechaty / 企业微信 API
- Discord — discord.py
- Slack — slack-bolt
- WhatsApp — Meta Cloud API
- LINE — Messaging API
- Matrix — matrix-nio
- Signal — signal-cli-rest-api
- iMessage — macOS AppleScript
- IRC — asyncio raw socket
- Google Chat — Webhook / API
- Email — SMTP + IMAP
- Microsoft Teams — Bot Framework
- SMS — Twilio
- Facebook Messenger — Graph API
- Twitter/X — DM API
- Reddit — asyncpraw

用法:
    from gateway.platforms.base import BasePlatformAdapter
    from gateway.platforms.telegram import TelegramAdapter
"""
