"""测试 — 上下文引用、身份模板、PII 脱敏、审计日志."""

from __future__ import annotations

import pytest


class TestContextReferences:
    @pytest.mark.asyncio
    async def test_resolve_file(self, tmp_path):
        from agent.core.context_references import ContextReferenceResolver

        (tmp_path / "test.py").write_text("print('hello')")
        resolver = ContextReferenceResolver(workdir=str(tmp_path))
        text, refs = await resolver.resolve("看看 @file:test.py 的内容")
        assert len(refs) == 1
        assert refs[0].ref_type == "file"
        assert "print('hello')" in refs[0].content
        assert "Attached Context" in text

    @pytest.mark.asyncio
    async def test_resolve_dir(self, tmp_path):
        from agent.core.context_references import ContextReferenceResolver

        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        resolver = ContextReferenceResolver(workdir=str(tmp_path))
        text, refs = await resolver.resolve("列出 @dir:.")
        assert len(refs) == 1
        assert refs[0].ref_type == "dir"

    @pytest.mark.asyncio
    async def test_resolve_no_refs(self):
        from agent.core.context_references import ContextReferenceResolver

        resolver = ContextReferenceResolver()
        text, refs = await resolver.resolve("普通文本没有引用")
        assert len(refs) == 0
        assert text == "普通文本没有引用"

    @pytest.mark.asyncio
    async def test_resolve_memory(self):
        from agent.core.context_references import ContextReferenceResolver

        resolver = ContextReferenceResolver()
        text, refs = await resolver.resolve("查找 @memory:编程偏好")
        assert len(refs) == 1
        assert refs[0].ref_type == "memory"

    @pytest.mark.asyncio
    async def test_resolve_nonexistent_file(self, tmp_path):
        from agent.core.context_references import ContextReferenceResolver

        resolver = ContextReferenceResolver(workdir=str(tmp_path))
        text, refs = await resolver.resolve("看 @file:不存在.py")
        assert len(refs) == 0


class TestAgentIdentity:
    def test_default_identity(self):
        from agent.core.identity import AgentIdentity

        identity = AgentIdentity()
        assert identity.name == "XJD Agent"
        prompt = identity.to_system_prompt()
        assert "XJD Agent" in prompt

    def test_builtin_identities(self):
        from agent.core.identity import BUILTIN_IDENTITIES

        assert "assistant" in BUILTIN_IDENTITIES
        assert "coder" in BUILTIN_IDENTITIES
        assert "researcher" in BUILTIN_IDENTITIES
        assert "ops" in BUILTIN_IDENTITIES

    def test_coder_identity(self):
        from agent.core.identity import BUILTIN_IDENTITIES

        coder = BUILTIN_IDENTITIES["coder"]
        prompt = coder.to_system_prompt()
        assert "编程" in prompt or "Coder" in prompt
        assert "代码" in prompt

    def test_save_load(self, tmp_path):
        from agent.core.identity import AgentIdentity

        identity = AgentIdentity(name="TestBot", role="测试机器人")
        path = tmp_path / "test.yaml"
        identity.save(path)
        assert path.exists()

        loaded = AgentIdentity.load("test", identities_dir=tmp_path)
        assert loaded.name == "TestBot"
        assert loaded.role == "测试机器人"

    def test_load_nonexistent(self, tmp_path):
        from agent.core.identity import AgentIdentity

        loaded = AgentIdentity.load("不存在", identities_dir=tmp_path)
        assert loaded.name == "XJD Agent"  # 回退到默认

    def test_list_available(self, tmp_path):
        from agent.core.identity import AgentIdentity

        (tmp_path / "bot1.yaml").write_text("name: Bot1")
        (tmp_path / "bot2.yml").write_text("name: Bot2")
        available = AgentIdentity.list_available(identities_dir=tmp_path)
        assert "bot1" in available
        assert "bot2" in available

    def test_to_system_prompt_with_rules(self):
        from agent.core.identity import AgentIdentity

        identity = AgentIdentity(
            name="Test",
            role="助手",
            rules=["规则1", "规则2"],
            restrictions=["限制1"],
        )
        prompt = identity.to_system_prompt()
        assert "规则1" in prompt
        assert "限制1" in prompt
