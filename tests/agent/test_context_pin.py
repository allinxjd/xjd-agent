"""测试 — ContextPinManager (pin CRUD, 上下文生成, activity)."""

from __future__ import annotations

import os
import tempfile

import pytest

from agent.context.pin_manager import ContextPinManager


async def _make_pm(tmpdir):
    """创建测试用 ContextPinManager."""
    workspace = os.path.join(tmpdir, "workspace")
    os.makedirs(workspace, exist_ok=True)
    db_path = os.path.join(tmpdir, "context.db")
    pm = ContextPinManager(workspace_dir=workspace, db_path=db_path)
    await pm.initialize()
    return pm, workspace


def _write_file(workspace, rel_path, content="hello world"):
    full = os.path.join(workspace, rel_path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)


# ── 初始化 ──

class TestInit:
    @pytest.mark.asyncio
    async def test_creates_tables(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            cursor = await pm._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            tables = {r[0] for r in await cursor.fetchall()}
            assert "context_pins" in tables
            assert "file_activity" in tables
            await pm.close()

    @pytest.mark.asyncio
    async def test_wal_mode(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            cursor = await pm._db.execute("PRAGMA journal_mode")
            mode = (await cursor.fetchone())[0]
            assert mode == "wal"
            await pm.close()


# ── Pin CRUD ──

class TestPinCRUD:
    @pytest.mark.asyncio
    async def test_add_pin(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "test.py")
            result = await pm.add_pin("test.py")
            assert result["duplicate"] is False
            assert "pin_id" in result
            await pm.close()

    @pytest.mark.asyncio
    async def test_add_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "test.py")
            r1 = await pm.add_pin("test.py")
            r2 = await pm.add_pin("test.py")
            assert r2["duplicate"] is True
            assert r2["pin_id"] == r1["pin_id"]
            await pm.close()

    @pytest.mark.asyncio
    async def test_sandbox_escape(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            with pytest.raises(ValueError, match="outside workspace"):
                await pm.add_pin("../../etc/passwd")
            await pm.close()

    @pytest.mark.asyncio
    async def test_remove_pin(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "test.py")
            r = await pm.add_pin("test.py")
            ok = await pm.remove_pin(r["pin_id"])
            assert ok is True
            pins = await pm.list_pins()
            assert len(pins) == 0
            await pm.close()

    @pytest.mark.asyncio
    async def test_update_pin(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "test.py")
            r = await pm.add_pin("test.py")
            ok = await pm.update_pin(r["pin_id"], active=0, max_lines=50)
            assert ok is True
            pins = await pm.list_pins()
            assert pins[0]["active"] is False
            assert pins[0]["max_lines"] == 50
            await pm.close()

    @pytest.mark.asyncio
    async def test_list_pins_with_exists(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "exists.py")
            await pm.add_pin("exists.py")
            await pm.add_pin("missing.py")
            pins = await pm.list_pins()
            assert len(pins) == 2
            assert pins[0]["exists"] is True or pins[1]["exists"] is True
            # missing.py doesn't exist
            missing = [p for p in pins if p["path"] == "missing.py"][0]
            assert missing["exists"] is False
            await pm.close()

    @pytest.mark.asyncio
    async def test_reorder_pins(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "a.py")
            _write_file(ws, "b.py")
            r1 = await pm.add_pin("a.py")
            r2 = await pm.add_pin("b.py")
            # Reorder: b first, then a
            await pm.reorder_pins([r2["pin_id"], r1["pin_id"]])
            pins = await pm.list_pins()
            assert pins[0]["path"] == "b.py"
            assert pins[1]["path"] == "a.py"
            await pm.close()


# ── 上下文生成 ──

class TestContextGeneration:
    @pytest.mark.asyncio
    async def test_empty_pins(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            ctx = await pm.get_pinned_context()
            assert ctx == ""
            await pm.close()

    @pytest.mark.asyncio
    async def test_file_content_injected(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "hello.py", "print('hello')\n")
            await pm.add_pin("hello.py")
            ctx = await pm.get_pinned_context()
            assert "hello.py" in ctx
            assert "print('hello')" in ctx
            assert "```python" in ctx
            await pm.close()

    @pytest.mark.asyncio
    async def test_directory_pin(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            os.makedirs(os.path.join(ws, "src"))
            _write_file(ws, "src/main.py")
            _write_file(ws, "src/utils.py")
            await pm.add_pin("src", pin_type="directory")
            ctx = await pm.get_pinned_context()
            assert "src/" in ctx
            assert "main.py" in ctx
            assert "utils.py" in ctx
            await pm.close()

    @pytest.mark.asyncio
    async def test_muted_pin_excluded(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "secret.py", "SECRET=123")
            r = await pm.add_pin("secret.py")
            await pm.update_pin(r["pin_id"], active=0)
            ctx = await pm.get_pinned_context()
            assert "SECRET" not in ctx
            await pm.close()

    @pytest.mark.asyncio
    async def test_missing_file_handled(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            await pm.add_pin("ghost.py")
            ctx = await pm.get_pinned_context()
            assert "文件不存在" in ctx
            await pm.close()

    @pytest.mark.asyncio
    async def test_binary_file_handled(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            full = os.path.join(ws, "data.bin")
            with open(full, "wb") as f:
                f.write(b"\x00\x01\x02\x03" * 100)
            await pm.add_pin("data.bin")
            ctx = await pm.get_pinned_context()
            assert "二进制文件" in ctx
            await pm.close()

    @pytest.mark.asyncio
    async def test_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            content = "\n".join(f"line {i}" for i in range(500))
            _write_file(ws, "big.py", content)
            r = await pm.add_pin("big.py", max_lines=50)
            ctx = await pm.get_pinned_context()
            assert "共 500 行" in ctx
            assert "显示前 50 行" in ctx
            await pm.close()


# ── Activity ──

class TestActivity:
    @pytest.mark.asyncio
    async def test_record_and_retrieve(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            await pm.record_activity("test.py", "read", "file_read", "content...")
            acts = await pm.get_recent_activity()
            assert len(acts) == 1
            assert acts[0]["path"] == "test.py"
            assert acts[0]["action"] == "read"
            await pm.close()

    @pytest.mark.asyncio
    async def test_activity_limit(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            for i in range(10):
                await pm.record_activity(f"file{i}.py", "read")
            acts = await pm.get_recent_activity(limit=5)
            assert len(acts) == 5
            await pm.close()


# ── Suggest Files ──

class TestSuggestFiles:
    @pytest.mark.asyncio
    async def test_suggest_by_keyword(self):
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "agent/memory/manager.py")
            _write_file(ws, "agent/core/engine.py")
            _write_file(ws, "tests/test_memory.py")
            results = await pm.suggest_files("memory")
            paths = [r["path"] for r in results]
            assert any("memory" in p for p in paths)
            await pm.close()

    @pytest.mark.asyncio
    async def test_suggest_empty_query(self):
        with tempfile.TemporaryDirectory() as d:
            pm, _ = await _make_pm(d)
            results = await pm.suggest_files("")
            assert results == []
            await pm.close()


# ── Budget & Validation ──

class TestBudgetAndValidation:
    @pytest.mark.asyncio
    async def test_budget_overflow(self):
        """超过 50K 预算时应截断."""
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            # 创建一个超大文件 (60K 字符)
            content = "x" * 60_000
            _write_file(ws, "huge.py", content)
            await pm.add_pin("huge.py", max_lines=9999)
            ctx = await pm.get_pinned_context()
            # 应该被截断到 50K 以内
            assert len(ctx) <= 55_000  # 允许一些格式开销
            assert "截断" in ctx
            await pm.close()

    @pytest.mark.asyncio
    async def test_invalid_pin_type_fallback(self):
        """无效 pin_type 应 fallback 到 file."""
        with tempfile.TemporaryDirectory() as d:
            pm, ws = await _make_pm(d)
            _write_file(ws, "test.py", "hello")
            r = await pm.add_pin("test.py", pin_type="invalid_type")
            assert r["duplicate"] is False
            pins = await pm.list_pins()
            assert pins[0]["pin_type"] == "file"
            await pm.close()
