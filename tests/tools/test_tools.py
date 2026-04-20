"""测试 — 工具系统 (Registry + Builtin + Extended)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from agent.tools.registry import ToolRegistry


class TestToolRegistry:
    def test_register(self):
        reg = ToolRegistry()

        async def handler(x: str) -> str:
            return x

        reg.register(
            name="echo",
            description="Echo input",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
            },
            handler=handler,
        )

        assert len(reg.list_tools()) == 1
        tool = reg.get("echo")  # actual method is `get` not `get_tool`
        assert tool is not None
        assert tool.name == "echo"

    def test_register_duplicate(self):
        reg = ToolRegistry()

        async def h1(**kw): return "1"
        async def h2(**kw): return "2"

        reg.register("t", "first", {}, h1)
        reg.register("t", "second", {}, h2)

        # 后注册覆盖
        tool = reg.get("t")
        assert tool.description == "second"

    def test_disable_enable(self):
        reg = ToolRegistry()

        async def h(**kw): return ""

        reg.register("t", "test", {}, h)
        assert reg.get("t").enabled is True

        reg.disable("t")
        assert reg.get("t").enabled is False

        reg.enable("t")
        assert reg.get("t").enabled is True

    def test_list_by_category(self):
        reg = ToolRegistry()

        async def h(**kw): return ""

        reg.register("a", "tool a", {}, h, category="web")
        reg.register("b", "tool b", {}, h, category="file")
        reg.register("c", "tool c", {}, h, category="web")

        all_tools = reg.list_tools()
        assert len(all_tools) == 3


class TestBuiltinTools:
    def test_register_builtin(self):
        from agent.tools.builtin import register_builtin_tools

        reg = ToolRegistry()
        register_builtin_tools(reg)

        tools = reg.list_tools()
        tool_names = [t.name for t in tools]

        assert "run_terminal" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "edit_file" in tool_names
        assert "list_directory" in tool_names

    @pytest.mark.asyncio
    async def test_read_file(self):
        from agent.tools.builtin import register_builtin_tools

        reg = ToolRegistry()
        register_builtin_tools(reg)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            tmp_path = f.name

        try:
            result = await reg.execute("read_file", {"path": tmp_path})
            assert "hello world" in result
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_list_directory(self):
        from agent.tools.builtin import register_builtin_tools

        reg = ToolRegistry()
        register_builtin_tools(reg)

        result = await reg.execute("list_directory", {"path": tempfile.gettempdir()})
        assert isinstance(result, str)


class TestExtendedTools:
    def test_register_extended(self):
        from agent.tools.extended import register_extended_tools

        reg = ToolRegistry()
        register_extended_tools(reg)

        tools = reg.list_tools()
        tool_names = [t.name for t in tools]

        assert "web_search" in tool_names
        assert "web_fetch" in tool_names
        assert "execute_code" in tool_names
        assert "grep_search" in tool_names
        assert "download_file" in tool_names

    @pytest.mark.asyncio
    async def test_execute_code_python(self):
        from agent.tools.extended import execute_code

        result = await execute_code(code="print(1 + 1)", language="python")
        assert "2" in result

    @pytest.mark.asyncio
    async def test_execute_code_timeout(self):
        from agent.tools.extended import execute_code

        result = await execute_code(
            code="import time; time.sleep(10)",
            language="python",
            timeout=1,
        )
        assert "超时" in result or "Timeout" in result.lower()

    @pytest.mark.asyncio
    async def test_grep_search(self):
        from agent.tools.extended import grep_search

        with tempfile.TemporaryDirectory() as d:
            Path(d, "test.py").write_text("def hello():\n    print('hello')\n")
            Path(d, "test2.py").write_text("def world():\n    print('world')\n")

            result = await grep_search(pattern="hello", path=d)
            assert "hello" in result
            assert "test.py" in result


class TestNewTools:
    """测试新增的 15+ 工具."""

    def test_all_26_tools_registered(self):
        from agent.tools.builtin import register_builtin_tools
        from agent.tools.extended import register_extended_tools

        reg = ToolRegistry()
        register_builtin_tools(reg)
        register_extended_tools(reg)

        tools = reg.list_tools()
        names = {t.name for t in tools}
        assert len(tools) >= 36

        # 新增工具
        for expected in [
            "browser_action", "system_info", "diff_files", "json_query",
            "clipboard_read", "clipboard_write", "screenshot", "archive_extract",
            "git_command", "process_manager", "http_request", "pdf_extract",
            "image_generate", "calendar_event", "text_to_speech", "speech_to_text",
            "database_query", "template_render", "text_transform", "regex_replace",
            "file_compress", "env_variable", "dns_lookup", "port_scanner",
            "cron_schedule", "api_mock",
        ]:
            assert expected in names, f"缺少工具: {expected}"

    @pytest.mark.asyncio
    async def test_system_info(self):
        from agent.tools.extended import _system_info
        result = await _system_info()
        assert "系统:" in result
        assert "CPU:" in result

    @pytest.mark.asyncio
    async def test_diff_files(self):
        from agent.tools.extended import _diff_files
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as a:
            a.write("line1\nline2\n")
            pa = a.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as b:
            b.write("line1\nline3\n")
            pb = b.name
        try:
            result = await _diff_files(pa, pb)
            assert "-line2" in result
            assert "+line3" in result
        finally:
            os.unlink(pa)
            os.unlink(pb)

    @pytest.mark.asyncio
    async def test_json_query(self):
        from agent.tools.extended import _json_query
        result = await _json_query(query="a.b", data='{"a": {"b": 42}}')
        assert "42" in result

    @pytest.mark.asyncio
    async def test_git_command_safe(self):
        from agent.tools.extended import _git_command
        result = await _git_command(subcommand="status", cwd=".")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_git_command_blocked(self):
        from agent.tools.extended import _git_command
        result = await _git_command(subcommand="rm")
        assert "不允许" in result

    @pytest.mark.asyncio
    async def test_archive_extract(self):
        from agent.tools.extended import _archive_extract
        import zipfile as zf
        with tempfile.TemporaryDirectory() as d:
            zpath = os.path.join(d, "test.zip")
            with zf.ZipFile(zpath, "w") as z:
                z.writestr("hello.txt", "world")
            dest = os.path.join(d, "out")
            result = await _archive_extract(zpath, dest)
            assert "已解压" in result
            assert Path(dest, "hello.txt").read_text() == "world"


class TestPhase2Tools:
    """测试 Phase 2 新增工具."""

    @pytest.mark.asyncio
    async def test_database_query(self):
        from agent.tools.extended import _database_query
        import aiosqlite
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            async with aiosqlite.connect(db_path) as db:
                await db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
                await db.execute("INSERT INTO t VALUES (1, 'alice')")
                await db.commit()
            result = await _database_query(db_path, "SELECT * FROM t")
            assert "alice" in result
        finally:
            os.unlink(db_path)

    @pytest.mark.asyncio
    async def test_template_render(self):
        from agent.tools.extended import _template_render
        result = await _template_render("Hello {{ name }}!", '{"name": "World"}')
        assert result == "Hello World!"

    @pytest.mark.asyncio
    async def test_text_transform(self):
        from agent.tools.extended import _text_transform
        assert await _text_transform("hello", "base64_encode") == "aGVsbG8="
        assert await _text_transform("aGVsbG8=", "base64_decode") == "hello"
        assert await _text_transform("hello", "upper") == "HELLO"
        assert len(await _text_transform("hello", "md5")) == 32

    @pytest.mark.asyncio
    async def test_regex_replace(self):
        from agent.tools.extended import _regex_replace
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("foo bar foo")
            tmp = f.name
        try:
            result = await _regex_replace(tmp, "foo", "baz")
            assert "2 处" in result
            assert Path(tmp).read_text() == "baz bar baz"
        finally:
            os.unlink(tmp)

    @pytest.mark.asyncio
    async def test_file_compress(self):
        from agent.tools.extended import _file_compress
        with tempfile.TemporaryDirectory() as d:
            Path(d, "a.txt").write_text("hello")
            out = os.path.join(d, "out.zip")
            result = await _file_compress(os.path.join(d, "a.txt"), out, "zip")
            assert "已创建" in result
            assert Path(out).exists()

    @pytest.mark.asyncio
    async def test_env_variable(self):
        from agent.tools.extended import _env_variable
        await _env_variable("set", "XJD_TEST_VAR", "123")
        result = await _env_variable("get", "XJD_TEST_VAR")
        assert "123" in result
        os.environ.pop("XJD_TEST_VAR", None)

    @pytest.mark.asyncio
    async def test_dns_lookup(self):
        from agent.tools.extended import _dns_lookup
        result = await _dns_lookup("localhost")
        assert "127.0.0.1" in result

    @pytest.mark.asyncio
    async def test_text_transform_unsupported(self):
        from agent.tools.extended import _text_transform
        result = await _text_transform("hello", "rot13")
        assert "不支持" in result
