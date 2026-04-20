"""测试 — 技能系统增强 (优化器/评估器/组合器/社区)."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from agent.skills.manager import Skill, SkillManager


@pytest.fixture
def tmp_skills_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def skill_manager(tmp_skills_dir):
    return SkillManager(skills_dir=tmp_skills_dir)


@pytest.fixture
def sample_skill():
    return Skill(
        skill_id="test-001",
        name="Deploy Project",
        description="Deploy a project to server",
        trigger="deploy project server",
        category="deploy",
        steps=[
            {"description": "Build project", "tool": "run_terminal", "args": {"command": "npm run build"}},
            {"description": "Upload to server", "tool": "run_terminal", "args": {"command": "scp -r dist/ server:/opt/app"}},
            {"description": "Restart service", "tool": "run_terminal", "args": {"command": "ssh server pm2 restart app"}},
        ],
        tags=["deploy", "server"],
        examples=["deploy to production", "push to server"],
        created_at=time.time(),
        updated_at=time.time(),
        version=1,
        use_count=10,
        success_rate=0.8,
    )


# ── Optimizer Tests ──────────────────────────────────────────

class TestSkillOptimizer:
    def test_record_failure(self, skill_manager):
        from agent.skills.optimizer import SkillOptimizer
        opt = SkillOptimizer(skill_manager)

        record = opt.record_failure("s1", "connection timeout", "deploy project", 2)
        assert record.error_info == "connection timeout"
        assert record.failed_step == 2
        assert opt.get_failure_count("s1") == 1

    def test_multiple_failures(self, skill_manager):
        from agent.skills.optimizer import SkillOptimizer
        opt = SkillOptimizer(skill_manager)

        for i in range(5):
            opt.record_failure("s1", f"error {i}")
        assert opt.get_failure_count("s1") == 5

    @pytest.mark.asyncio
    async def test_refine_skill(self, skill_manager, sample_skill):
        from agent.skills.optimizer import SkillOptimizer

        await skill_manager.load_skills()
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        opt = SkillOptimizer(skill_manager)
        result = await opt.refine_skill(
            skill_id=sample_skill.skill_id,
            new_steps=[{"description": "Improved step", "tool": "run_terminal"}],
            reason="test refinement",
        )
        assert result is not None
        assert result.success is True
        assert result.new_version == 2

    @pytest.mark.asyncio
    async def test_check_deprecation(self, skill_manager, sample_skill):
        from agent.skills.optimizer import SkillOptimizer

        await skill_manager.load_skills()
        sample_skill.success_rate = 0.1
        sample_skill.use_count = 10
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        opt = SkillOptimizer(skill_manager, min_success_rate_threshold=0.3)
        deprecated = await opt.check_deprecation(sample_skill.skill_id)
        assert deprecated is True

    @pytest.mark.asyncio
    async def test_no_deprecation_healthy_skill(self, skill_manager, sample_skill):
        from agent.skills.optimizer import SkillOptimizer

        await skill_manager.load_skills()
        sample_skill.success_rate = 0.9
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        opt = SkillOptimizer(skill_manager)
        deprecated = await opt.check_deprecation(sample_skill.skill_id)
        assert deprecated is False


# ── Evaluator Tests ──────────────────────────────────────────

class TestSkillEvaluator:
    @pytest.mark.asyncio
    async def test_score_skill(self, skill_manager, sample_skill):
        from agent.skills.evaluator import SkillEvaluator

        await skill_manager.load_skills()
        sample_skill.updated_at = time.time()
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        evaluator = SkillEvaluator(skill_manager)
        score = await evaluator.score_skill(sample_skill.skill_id)
        assert score is not None
        assert score.effectiveness > 0
        assert score.recommendation == "keep"

    @pytest.mark.asyncio
    async def test_score_low_success(self, skill_manager, sample_skill):
        from agent.skills.evaluator import SkillEvaluator

        await skill_manager.load_skills()
        sample_skill.success_rate = 0.2
        sample_skill.updated_at = time.time() - 86400 * 60  # 60 days old
        skill_manager._skills[sample_skill.skill_id] = sample_skill

        evaluator = SkillEvaluator(skill_manager)
        score = await evaluator.score_skill(sample_skill.skill_id)
        assert score.recommendation in ("optimize", "deprecate")

    @pytest.mark.asyncio
    async def test_system_health(self, skill_manager, sample_skill):
        from agent.skills.evaluator import SkillEvaluator

        await skill_manager.load_skills()
        sample_skill.updated_at = time.time()
        skill_manager._skills[sample_skill.skill_id] = sample_skill

        evaluator = SkillEvaluator(skill_manager)
        health = await evaluator.get_system_health()
        assert health.total_skills == 1
        assert health.active_skills >= 0


# ── Community Tests ──────────────────────────────────────────

class TestSkillCommunity:
    def test_skill_to_markdown(self, skill_manager, sample_skill):
        from agent.skills.community import SkillCommunity
        community = SkillCommunity(skill_manager)

        md = community.skill_to_markdown(sample_skill)
        assert "# Deploy Project" in md
        assert "## Steps" in md
        assert "Build project" in md
        assert "deploy" in md.lower()

    def test_markdown_roundtrip(self, skill_manager, sample_skill):
        from agent.skills.community import SkillCommunity
        community = SkillCommunity(skill_manager)

        md = community.skill_to_markdown(sample_skill)
        data = community.markdown_to_skill_data(md)
        assert data is not None
        assert data["name"] == "Deploy Project"
        assert len(data["steps"]) >= 1
        assert data["category"] == "deploy"

    @pytest.mark.asyncio
    async def test_export_import(self, skill_manager, sample_skill):
        from agent.skills.community import SkillCommunity

        await skill_manager.load_skills()
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        community = SkillCommunity(skill_manager)

        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "test_skill.md")
            ok = await community.export_skill(sample_skill.skill_id, path)
            assert ok is True
            assert Path(path).exists()

            # Import into fresh manager
            mgr2 = SkillManager(skills_dir=os.path.join(d, "imported"))
            await mgr2.load_skills()
            community2 = SkillCommunity(mgr2)
            sid = await community2.import_skill(path)
            assert sid is not None

    @pytest.mark.asyncio
    async def test_export_all(self, skill_manager, sample_skill):
        from agent.skills.community import SkillCommunity

        await skill_manager.load_skills()
        skill_manager._skills[sample_skill.skill_id] = sample_skill

        community = SkillCommunity(skill_manager)
        with tempfile.TemporaryDirectory() as d:
            count = await community.export_all(d)
            assert count == 1
            assert (Path(d) / "INDEX.md").exists()


# ── Composer Tests ────────────────────────────────────────────

class TestSkillComposer:
    @pytest.mark.asyncio
    async def test_create_pipeline(self, skill_manager):
        from agent.skills.composer import SkillComposer

        with tempfile.TemporaryDirectory() as d:
            composer = SkillComposer(skill_manager, pipelines_dir=d)
            pipeline = await composer.create_pipeline(
                name="Full Deploy",
                description="Build + Deploy + Verify",
                skill_ids=["s1", "s2", "s3"],
            )
            assert pipeline.name == "Full Deploy"
            assert len(pipeline.steps) == 3

    @pytest.mark.asyncio
    async def test_list_pipelines(self, skill_manager):
        from agent.skills.composer import SkillComposer

        with tempfile.TemporaryDirectory() as d:
            composer = SkillComposer(skill_manager, pipelines_dir=d)
            await composer.create_pipeline("P1", "desc", ["s1"])
            await composer.create_pipeline("P2", "desc", ["s2"])

            pipelines = await composer.list_pipelines()
            assert len(pipelines) == 2

    @pytest.mark.asyncio
    async def test_delete_pipeline(self, skill_manager):
        from agent.skills.composer import SkillComposer

        with tempfile.TemporaryDirectory() as d:
            composer = SkillComposer(skill_manager, pipelines_dir=d)
            p = await composer.create_pipeline("P1", "desc", ["s1"])
            assert await composer.delete_pipeline(p.pipeline_id) is True
            assert await composer.get_pipeline(p.pipeline_id) is None

    @pytest.mark.asyncio
    async def test_resolve_pipeline(self, skill_manager, sample_skill):
        from agent.skills.composer import SkillComposer

        await skill_manager.load_skills()
        skill_manager._skills[sample_skill.skill_id] = sample_skill

        with tempfile.TemporaryDirectory() as d:
            composer = SkillComposer(skill_manager, pipelines_dir=d)
            p = await composer.create_pipeline("P1", "desc", [sample_skill.skill_id])
            skills = await composer.resolve_pipeline(p.pipeline_id)
            assert len(skills) == 1
            assert skills[0].name == "Deploy Project"


# ── Manager Failure Tracking Tests ────────────────────────────

class TestManagerFailureTracking:
    @pytest.mark.asyncio
    async def test_record_failure(self, skill_manager, sample_skill):
        await skill_manager.load_skills()
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        old_rate = sample_skill.success_rate
        await skill_manager.record_failure(sample_skill.skill_id, "test error")

        skill = await skill_manager.get_skill(sample_skill.skill_id)
        assert skill.failure_count == 1
        assert skill.success_rate < old_rate


# ── Learning Loop Failure Tests ───────────────────────────────

class TestLearningLoopFailure:
    @pytest.mark.asyncio
    async def test_on_turn_failed(self, skill_manager, sample_skill):
        from agent.skills.learning_loop import LearningLoop

        await skill_manager.load_skills()
        skill_manager._skills[sample_skill.skill_id] = sample_skill
        await skill_manager._save_skill(sample_skill)

        loop = LearningLoop(skill_manager=skill_manager)
        report = await loop.on_turn_failed(
            messages=[{"role": "user", "content": "deploy project"}],
            error_info="connection refused",
            matched_skill_id=sample_skill.skill_id,
        )
        assert report["actions"]
        assert any(a["type"] == "skill_failure_recorded" for a in report["actions"])
        assert loop.stats.failed_turns == 1

    @pytest.mark.asyncio
    async def test_on_turn_failed_no_skill(self, skill_manager):
        from agent.skills.learning_loop import LearningLoop

        loop = LearningLoop(skill_manager=skill_manager)
        report = await loop.on_turn_failed(
            messages=[{"role": "user", "content": "hello"}],
            error_info="some error",
        )
        assert report["actions"] == []
        assert loop.stats.failed_turns == 1


# ── Procedural Bridge Tests ───────────────────────────────────

class TestProceduralBridge:
    @pytest.mark.asyncio
    async def test_skill_create_syncs_to_memory(self, tmp_skills_dir, sample_skill):
        from agent.memory.provider import BuiltinMemoryProvider, MemoryType
        from agent.memory.manager import MemoryManager
        from agent.skills.procedural_bridge import ProceduralBridge

        provider = BuiltinMemoryProvider(db_path=os.path.join(tmp_skills_dir, "mem.db"))
        mm = MemoryManager(provider=provider)
        await mm.initialize()

        bridge = ProceduralBridge(mm)
        mid = await bridge.on_skill_created(sample_skill)
        assert mid is not None

        # 验证记忆可以搜到
        results = await mm.recall("Deploy Project", memory_types=[MemoryType.PROCEDURAL])
        assert len(results) >= 1
        assert "Deploy Project" in results[0].memory.content

        await provider.close()

    @pytest.mark.asyncio
    async def test_skill_update_syncs(self, tmp_skills_dir, sample_skill):
        from agent.memory.provider import BuiltinMemoryProvider, MemoryType
        from agent.memory.manager import MemoryManager
        from agent.skills.procedural_bridge import ProceduralBridge

        provider = BuiltinMemoryProvider(db_path=os.path.join(tmp_skills_dir, "mem.db"))
        mm = MemoryManager(provider=provider)
        await mm.initialize()

        bridge = ProceduralBridge(mm)
        await bridge.on_skill_created(sample_skill)

        sample_skill.description = "Updated deploy process"
        ok = await bridge.on_skill_updated(sample_skill)
        assert ok is True

        await provider.close()

    @pytest.mark.asyncio
    async def test_skill_delete_cleans_memory(self, tmp_skills_dir, sample_skill):
        from agent.memory.provider import BuiltinMemoryProvider, MemoryType
        from agent.memory.manager import MemoryManager
        from agent.skills.procedural_bridge import ProceduralBridge

        provider = BuiltinMemoryProvider(db_path=os.path.join(tmp_skills_dir, "mem.db"))
        mm = MemoryManager(provider=provider)
        await mm.initialize()

        bridge = ProceduralBridge(mm)
        await bridge.on_skill_created(sample_skill)

        deleted = await bridge.on_skill_deleted(sample_skill.skill_id, sample_skill.name)
        assert deleted is True

        await provider.close()

    @pytest.mark.asyncio
    async def test_manager_with_bridge(self, tmp_skills_dir):
        from agent.memory.provider import BuiltinMemoryProvider, MemoryType
        from agent.memory.manager import MemoryManager
        from agent.skills.procedural_bridge import ProceduralBridge

        provider = BuiltinMemoryProvider(db_path=os.path.join(tmp_skills_dir, "mem.db"))
        mm = MemoryManager(provider=provider)
        await mm.initialize()

        mgr = SkillManager(skills_dir=tmp_skills_dir)
        bridge = ProceduralBridge(mm)
        mgr.set_procedural_bridge(bridge)

        skill = await mgr.create_skill(
            name="Test Skill",
            description="A test",
            trigger="test",
            steps=[{"description": "step 1"}],
        )

        # 验证记忆已同步
        results = await mm.recall("Test Skill", memory_types=[MemoryType.PROCEDURAL])
        assert len(results) >= 1

        # 删除技能
        await mgr.delete_skill(skill.skill_id)

        await provider.close()
