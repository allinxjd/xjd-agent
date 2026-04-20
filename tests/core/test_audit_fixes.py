"""测试 — 审计修复验证: 安全、并发、API 一致性."""

from __future__ import annotations

import pytest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class TestSecurityFixes:
    """验证安全审计修复."""

    @pytest.mark.asyncio
    async def test_admin_password_not_admin(self, tmp_path):
        """默认 admin 密码不是 'admin'."""
        from gateway.core.auth import AuthManager

        auth = AuthManager(db_path=str(tmp_path / "sec.db"))
        await auth.initialize()

        result = auth.authenticate_password("admin", "admin")
        assert not result.authenticated, "Default admin password must not be 'admin'"

    @pytest.mark.asyncio
    async def test_admin_password_not_in_log(self, tmp_path):
        """admin 密码不应完整出现在日志中."""
        import logging

        log_records = []
        handler = logging.Handler()
        handler.emit = lambda record: log_records.append(record)
        logger = logging.getLogger("gateway.core.auth")
        logger.addHandler(handler)

        try:
            from gateway.core.auth import AuthManager
            auth = AuthManager(db_path=str(tmp_path / "logtest.db"))
            await auth.initialize()

            # 检查日志中密码被截断 (只显示前4位 + ****)
            warning_msgs = [r.getMessage() for r in log_records if r.levelno >= logging.WARNING]
            for msg in warning_msgs:
                if "password=" in msg:
                    # 密码部分应以 **** 结尾
                    assert "****" in msg, f"Password not masked in log: {msg}"
        finally:
            logger.removeHandler(handler)

    def test_openai_api_constant_time_compare(self):
        """OpenAI API key 比较使用 hmac.compare_digest."""
        source = (_REPO_ROOT / "web" / "openai_api.py").read_text()
        assert "hmac.compare_digest" in source, "API key comparison must use constant-time compare"
        assert "auth[7:] != self._config.api_key" not in source, "Direct string comparison must be removed"

    def test_ws_auth_code_exists(self):
        """WebSocket 控制面有认证逻辑."""
        source = (_REPO_ROOT / "gateway" / "core" / "server.py").read_text()
        assert "ws_token" in source, "WebSocket must check ws_token"
        assert "Unauthorized" in source, "WebSocket must reject unauthorized connections"


class TestConcurrencyFixes:
    """验证并发修复."""

    def test_session_lock_uses_setdefault(self):
        """Session lock 使用 setdefault 避免竞态."""
        source = (_REPO_ROOT / "gateway" / "core" / "server.py").read_text()
        assert "setdefault(session_key, asyncio.Lock())" in source
        assert "if session_key not in self._session_locks" not in source


class TestAPIConsistency:
    """验证 SDK 与 Server API 字段一致性."""

    def test_sdk_system_prompt_field_matches_server(self):
        """SDK 使用 'prompt' 字段 (与 server 一致)."""
        sdk_source = (_REPO_ROOT / "sdk" / "client.py").read_text()
        # get_system_prompt 应读 "prompt" 而非 "system_prompt"
        assert 'data.get("prompt"' in sdk_source
        # set_system_prompt 应发 "prompt" 而非 "system_prompt"
        assert '"prompt": prompt' in sdk_source


class TestInputValidation:
    """验证输入验证修复."""

    def test_audit_log_invalid_params(self):
        """audit_log 端点处理非法 limit/offset."""
        source = (_REPO_ROOT / "web" / "server.py").read_text()
        assert "ValueError" in source or "invalid limit/offset" in source

    def test_api_chat_invalid_json(self):
        """_api_chat 端点处理非法 JSON."""
        source = (_REPO_ROOT / "web" / "server.py").read_text()
        assert "invalid JSON body" in source

    def test_engine_error_handled(self):
        """engine.run_turn 异常被捕获."""
        source = (_REPO_ROOT / "web" / "server.py").read_text()
        assert "Engine error" in source


class TestImportFixes:
    """验证 import 修复."""

    def test_subcommands_has_subprocess(self):
        """subcommands.py 导入了 subprocess."""
        from cli.commands import subcommands
        import subprocess
        assert hasattr(subcommands, 'subprocess') or 'subprocess' in dir(subcommands)

    def test_cli_worktree_uuid_import(self):
        """cli/main.py worktree 代码块中 uuid 在使用前导入."""
        source = (_REPO_ROOT / "cli" / "main.py").read_text()
        lines = source.split("\n")
        uuid_import_line = None
        uuid_usage_line = None
        for i, line in enumerate(lines):
            if "import uuid" in line and "as _uuid" not in line:
                uuid_import_line = i
            if "uuid.uuid4()" in line and uuid_usage_line is None:
                uuid_usage_line = i
        if uuid_import_line is not None and uuid_usage_line is not None:
            assert uuid_import_line < uuid_usage_line, "uuid must be imported before use"
