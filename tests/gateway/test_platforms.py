"""Tests for gateway.platforms — 平台适配器."""

import asyncio
import pytest
from gateway.platforms.base import (
    BasePlatformAdapter,
    ChatType,
    MessageType,
    OutgoingMessage,
    PlatformChat,
    PlatformEvent,
    PlatformMessage,
    PlatformType,
    PlatformUser,
    EventType,
)
from gateway.platforms.discord import DiscordAdapter
from gateway.platforms.slack import SlackAdapter
from gateway.platforms.telegram import TelegramAdapter
from gateway.platforms.wechat import WeChatAdapter
from gateway.platforms.feishu import FeishuAdapter
from gateway.platforms.dingtalk import DingTalkAdapter
from gateway.platforms.whatsapp import WhatsAppAdapter
from gateway.platforms.line import LineAdapter
from gateway.platforms.matrix import MatrixAdapter
from gateway.platforms.web import WebPlatformAdapter as WebAdapter
from gateway.platforms.signal import SignalAdapter
from gateway.platforms.email import EmailAdapter
from gateway.platforms.imessage import IMessageAdapter
from gateway.platforms.irc_adapter import IRCAdapter
from gateway.platforms.google_chat import GoogleChatAdapter
from gateway.platforms.teams import TeamsAdapter
from gateway.platforms.sms import SMSAdapter
from gateway.platforms.facebook import FacebookAdapter
from gateway.platforms.twitter import TwitterAdapter
from gateway.platforms.reddit import RedditAdapter


class TestPlatformModels:
    def test_platform_message(self):
        msg = PlatformMessage(
            message_id="m1",
            platform=PlatformType.DISCORD,
            chat=PlatformChat(chat_id="c1", chat_type=ChatType.GROUP),
            sender=PlatformUser(user_id="u1", username="alice"),
            content="hello",
        )
        assert msg.message_id == "m1"
        assert msg.platform == PlatformType.DISCORD
        assert msg.sender.username == "alice"
        assert msg.message_type == MessageType.TEXT

    def test_outgoing_message(self):
        out = OutgoingMessage(chat_id="c1", content="reply")
        assert out.chat_id == "c1"
        assert out.message_type == MessageType.TEXT

    def test_platform_event(self):
        ev = PlatformEvent(
            event_type=EventType.FRIEND_REQUEST,
            platform=PlatformType.WECHAT,
            data={"from": "u2"},
        )
        assert ev.event_type == EventType.FRIEND_REQUEST


class TestAdapterInit:
    """Test that all adapters can be instantiated without external deps."""

    def test_discord(self):
        a = DiscordAdapter({"bot_token": "test"})
        assert a.name == "Discord"
        assert a.platform_type == PlatformType.DISCORD
        assert a.capabilities["text"] is True
        assert a.capabilities["thread"] is True
        assert not a.is_running

    def test_slack(self):
        a = SlackAdapter({"bot_token": "xoxb-test", "app_token": "xapp-test"})
        assert a.name == "Slack"
        assert a.capabilities["block_kit"] is True

    def test_telegram(self):
        a = TelegramAdapter({"bot_token": "123:ABC"})
        assert a.name == "Telegram"

    def test_wechat(self):
        a = WeChatAdapter({})
        assert a.name in ("WeChat", "企业微信", "微信")

    def test_feishu(self):
        a = FeishuAdapter({"app_id": "x", "app_secret": "y"})
        assert a.name in ("Feishu", "飞书")

    def test_dingtalk(self):
        a = DingTalkAdapter({"app_key": "k", "app_secret": "s"})
        assert a.name in ("DingTalk", "钉钉")

    def test_whatsapp(self):
        a = WhatsAppAdapter({"phone_number_id": "123", "access_token": "t"})
        assert a.name == "WhatsApp"

    def test_line(self):
        a = LineAdapter({"channel_secret": "s", "channel_access_token": "t"})
        assert a.name == "LINE"

    def test_matrix(self):
        a = MatrixAdapter({"homeserver": "https://matrix.org", "access_token": "t"})
        assert a.name == "Matrix"

    def test_web(self):
        a = WebAdapter({})
        assert a.name == "Web"

    def test_signal(self):
        a = SignalAdapter({"phone_number": "+1234567890", "api_url": "http://localhost:8080"})
        assert a.name == "Signal"
        assert a.platform_type == PlatformType.SIGNAL
        assert a.capabilities["text"] is True
        assert not a.is_running

    def test_email(self):
        a = EmailAdapter({"username": "test@example.com", "password": "pass"})
        assert a.name == "Email"
        assert a.platform_type == PlatformType.EMAIL
        assert a.capabilities["rich_text"] is True

    def test_imessage(self):
        a = IMessageAdapter({})
        assert a.name == "iMessage"
        assert a.platform_type == PlatformType.IMESSAGE

    def test_irc(self):
        a = IRCAdapter({"server": "irc.libera.chat", "nickname": "testbot", "channels": ["#test"]})
        assert a.name == "IRC"
        assert a.platform_type == PlatformType.IRC
        assert a.capabilities["text"] is True
        assert a.capabilities["image"] is False

    def test_google_chat(self):
        a = GoogleChatAdapter({"webhook_url": "https://chat.googleapis.com/v1/spaces/xxx/messages"})
        assert a.name == "Google Chat"
        assert a.platform_type == PlatformType.GOOGLE_CHAT
        assert a.capabilities["thread"] is True

    def test_teams(self):
        a = TeamsAdapter({"app_id": "test", "app_password": "test"})
        assert a.name == "Microsoft Teams"
        assert a.platform_type == PlatformType.TEAMS
        assert a.capabilities["text"] is True
        assert a.capabilities["thread"] is True
        assert not a.is_running

    def test_sms(self):
        a = SMSAdapter({"account_sid": "AC123", "auth_token": "tok", "from_number": "+1234"})
        assert a.name == "SMS"
        assert a.platform_type == PlatformType.SMS
        assert a.capabilities["text"] is True
        assert a.capabilities["file"] is False

    def test_facebook(self):
        a = FacebookAdapter({"page_access_token": "tok", "verify_token": "v"})
        assert a.name == "Facebook Messenger"
        assert a.platform_type == PlatformType.FACEBOOK
        assert a.capabilities["text"] is True
        assert a.capabilities["typing_indicator"] is True

    def test_twitter(self):
        a = TwitterAdapter({"bearer_token": "tok"})
        assert a.name == "Twitter/X"
        assert a.platform_type == PlatformType.TWITTER
        assert a.capabilities["text"] is True
        assert a.capabilities["thread"] is False

    def test_reddit(self):
        a = RedditAdapter({"client_id": "cid", "client_secret": "cs", "username": "bot"})
        assert a.name == "Reddit"
        assert a.platform_type == PlatformType.REDDIT
        assert a.capabilities["rich_text"] is True
        assert a.capabilities["thread"] is True


class TestBaseDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_message(self):
        a = WebAdapter({})
        received = []
        a.on_message(lambda msg: received.append(msg) or asyncio.sleep(0))

        msg = PlatformMessage(
            message_id="m1",
            platform=PlatformType.WEB,
            chat=PlatformChat(chat_id="c1"),
            sender=PlatformUser(user_id="u1"),
            content="test",
        )
        await a._dispatch_message(msg)
        assert len(received) == 1
        assert received[0].content == "test"

    @pytest.mark.asyncio
    async def test_dispatch_event(self):
        a = WebAdapter({})
        events = []
        a.on_event(lambda ev: events.append(ev) or asyncio.sleep(0))

        ev = PlatformEvent(
            event_type=EventType.TYPING,
            platform=PlatformType.WEB,
        )
        await a._dispatch_event(ev)
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_handler_error_doesnt_crash(self):
        a = WebAdapter({})

        async def bad_handler(msg):
            raise ValueError("boom")

        a.on_message(bad_handler)
        msg = PlatformMessage(
            message_id="m1",
            platform=PlatformType.WEB,
            chat=PlatformChat(chat_id="c1"),
            sender=PlatformUser(user_id="u1"),
        )
        # Should not raise
        await a._dispatch_message(msg)


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_default_health(self):
        a = WebAdapter({})
        h = await a.health_check()
        assert h["platform"] == "web"
        assert h["running"] is False
