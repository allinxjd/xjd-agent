"""测试 — Gateway Session 管理."""

from __future__ import annotations

import os
import tempfile

import pytest

from gateway.platforms.base import PlatformType


class TestSession:
    def test_create_session(self):
        from gateway.core.session import Session
        s = Session(
            session_id="test-1",
            user_id="user-1",
            platform=PlatformType.TELEGRAM,
            chat_id="chat-1",
        )
        assert s.session_id == "test-1"
        assert s.is_active is True

    def test_session_fields(self):
        from gateway.core.session import Session
        s = Session(
            session_id="test-1",
            user_id="user-1",
            platform=PlatformType.TELEGRAM,
            chat_id="chat-1",
        )
        assert s.user_id == "user-1"
        assert s.platform == PlatformType.TELEGRAM
        assert s.chat_id == "chat-1"
        assert s.messages == []


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_get_or_create(self):
        from gateway.core.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "sessions.db")
            sm = SessionManager(db_path=db_path)
            # SessionManager auto-initializes via _ensure_db()

            session = await sm.get_or_create(
                user_id="user-1",
                platform=PlatformType.TELEGRAM,
                chat_id="chat-1",
            )

            assert session is not None
            assert session.user_id == "user-1"

            # 再次获取应该是同一个
            session2 = await sm.get_or_create(
                user_id="user-1",
                platform=PlatformType.TELEGRAM,
                chat_id="chat-1",
            )

            assert session2.session_id == session.session_id

    @pytest.mark.asyncio
    async def test_multiple_sessions(self):
        from gateway.core.session import SessionManager

        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "sessions.db")
            sm = SessionManager(db_path=db_path)

            s1 = await sm.get_or_create("user-1", PlatformType.TELEGRAM, "chat-1")
            s2 = await sm.get_or_create("user-2", PlatformType.FEISHU, "chat-2")

            # Different users should get different sessions
            assert s1.user_id != s2.user_id
            assert s1.session_id != s2.session_id
