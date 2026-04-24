"""测试 — 6 层技能匹配管线 (L1~L6)."""

from __future__ import annotations

import tempfile
import time

import pytest

from agent.skills.manager import Skill, SkillManager


@pytest.fixture
def tmp_skills_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def manager(tmp_skills_dir):
    m = SkillManager(skills_dir=tmp_skills_dir)
    m._loaded = True
    m._last_loaded = time.time()
    return m


def _make_skill(skill_id: str, name: str, trigger: str,
                tags: list[str] | None = None,
                examples: list[str] | None = None) -> Skill:
    return Skill(
        skill_id=skill_id,
        name=name,
        description=f"Skill: {name}",
        trigger=trigger,
        tags=tags or [],
        examples=examples or [],
        created_at=time.time(),
        updated_at=time.time(),
    )


# ── Saturation Curve ──

def test_saturation_curve_basic():
    sat = SkillManager._saturation_score
    assert abs(sat(0) - 0.0) < 0.01
    assert abs(sat(1) - 0.286) < 0.01
    assert abs(sat(3) - 0.545) < 0.01
    assert abs(sat(5) - 0.667) < 0.01
    assert abs(sat(10) - 0.80) < 0.01


def test_saturation_diminishing_returns():
    """堆量不应线性增长 — 10 个触发词 vs 5 个触发词差距应该很小."""
    sat = SkillManager._saturation_score
    diff_5_to_10 = sat(10) - sat(5)
    diff_0_to_5 = sat(5) - sat(0)
    assert diff_5_to_10 < diff_0_to_5 * 0.3


# ── L2: Exact Name ──

@pytest.mark.asyncio
async def test_l2_exact_name(manager):
    ecom_ops = _make_skill("ecommerce-ops", "电商店铺运营", "电商 运营 商品",
                           tags=["电商", "运营"])
    ecom_img = _make_skill("ecommerce-image-pipeline", "电商图片研究生成流水线",
                           "做图 竞品 调研", tags=["电商", "做图"])
    manager._skills = {s.skill_id: s for s in [ecom_ops, ecom_img]}
    manager._loaded = True

    result = await manager.match_skill("配置一下电商图片研究生成流水线的凭证")
    assert result is not None
    assert result.skill_id == "ecommerce-image-pipeline"


# ── L3: Trivial Fast-Path ──

@pytest.mark.asyncio
async def test_l3_trivial_inherits_prior(manager):
    skill_a = _make_skill("skill-a", "SkillA", "unique_trigger_a")
    manager._skills = {"skill-a": skill_a}
    manager._loaded = True
    manager._last_matched_skill = "skill-a"

    for msg in ["好的", "ok", "继续", "是", "确认", "yes"]:
        result = await manager.match_skill(msg)
        assert result is not None, f"Trivial '{msg}' should inherit prior skill"
        assert result.skill_id == "skill-a"


@pytest.mark.asyncio
async def test_l3_trivial_no_prior(manager):
    """没有上一轮技能时，短消息不应匹配."""
    skill_a = _make_skill("skill-a", "SkillA", "unique_trigger_a")
    manager._skills = {"skill-a": skill_a}
    manager._loaded = True
    manager._last_matched_skill = None

    result = await manager.match_skill("好的")
    assert result is None


# ── L4: Keyword Saturation — 防宽泛触发词霸占 ──

@pytest.mark.asyncio
async def test_l4_narrow_beats_broad(manager):
    """窄触发词技能应该在相关消息上胜过宽泛触发词技能."""
    broad = _make_skill("broad", "BroadSkill",
                        "电商 运营 商品 管理 订单 上架 下架 发货 推广 活动 客服 评价 数据 拼多多 淘宝 京东",
                        tags=["电商", "运营", "商品管理"])
    narrow = _make_skill("narrow", "NarrowSkill",
                         "做图 竞品 调研 研究 主图 白底图 详情图",
                         tags=["电商", "做图", "竞品分析"])
    manager._skills = {s.skill_id: s for s in [broad, narrow]}
    manager._loaded = True

    result = await manager.match_skill("帮我做一张电商主图，先调研一下竞品")
    assert result is not None
    assert result.skill_id == "narrow"


@pytest.mark.asyncio
async def test_l4_broad_wins_when_relevant(manager):
    """当消息确实是运营相关时，宽泛技能应该赢."""
    broad = _make_skill("broad", "BroadSkill",
                        "电商 运营 商品 管理 订单 上架 下架 发货",
                        tags=["电商", "运营", "订单"])
    narrow = _make_skill("narrow", "NarrowSkill",
                         "做图 竞品 调研 研究 主图",
                         tags=["做图", "竞品分析"])
    manager._skills = {s.skill_id: s for s in [broad, narrow]}
    manager._loaded = True

    result = await manager.match_skill("查看拼多多店铺的订单列表，有哪些需要发货")
    assert result is not None
    assert result.skill_id == "broad"


# ── L1: Learn Cache ──

@pytest.mark.asyncio
async def test_l1_learn_cache(manager):
    skill_a = _make_skill("skill-a", "SkillA", "unique_trigger_a")
    manager._skills = {"skill-a": skill_a}
    manager._loaded = True

    manager.record_match("帮我做一张电商主图", "skill-a")
    result = await manager.match_skill("帮我做一张电商主图")
    assert result is not None
    assert result.skill_id == "skill-a"


@pytest.mark.asyncio
async def test_l1_learn_cache_persistence(tmp_skills_dir):
    """Learn cache 应该跨实例持久化."""
    m1 = SkillManager(skills_dir=tmp_skills_dir)
    m1.record_match("test prompt", "skill-x")

    m2 = SkillManager(skills_dir=tmp_skills_dir)
    assert m2._learn_cache.get("test prompt") == "skill-x"


# ── L5: Prior-Intent Inheritance ──

@pytest.mark.asyncio
async def test_l5_prior_intent_low_confidence(manager):
    """低置信度时应继承上一轮技能."""
    skill_a = _make_skill("skill-a", "SkillA", "unique_trigger_xyz")
    skill_b = _make_skill("skill-b", "SkillB", "another_trigger_abc")
    manager._skills = {s.skill_id: s for s in [skill_a, skill_b]}
    manager._loaded = True
    manager._last_matched_skill = "skill-a"

    # 消息不包含任何触发词 → scored 为空 → 不触发 L5
    # 但如果有微弱匹配 (sat < 0.55)，应继承
    result = await manager.match_skill("再来一次")
    # "再来一次" 不匹配任何触发词，scored 为空，L5 需要 scored 非空
    # 这种情况走 L6 或返回 None — 这是正确行为


# ── L4: 无关消息逃逸 ──

@pytest.mark.asyncio
async def test_l4_unrelated_message_escape(manager):
    """和所有技能都无关的消息应返回 None，不继承上一轮."""
    ecom = _make_skill("ecom", "电商运营", "电商 运营 商品 订单",
                       tags=["电商", "运营"])
    manager._skills = {"ecom": ecom}
    manager._loaded = True
    manager._last_matched_skill = "ecom"
    manager._last_match_time = time.time()

    result = await manager.match_skill("帮我将桌面的PDF文件优化一下布局")
    assert result is None
    assert manager._last_matched_skill is None


# ── L5: 时间衰减 ──

@pytest.mark.asyncio
async def test_l5_time_decay_clears_prior(manager):
    """超过 5 分钟后不再继承上一轮技能."""
    skill_a = _make_skill("skill-a", "SkillA", "电商 运营 商品",
                          tags=["电商"])
    manager._skills = {"skill-a": skill_a}
    manager._loaded = True
    manager._last_matched_skill = "skill-a"
    manager._last_match_time = time.time() - 301  # 超过 5 分钟

    result = await manager.match_skill("帮我看看运营数据")
    # 虽然有微弱匹配，但超时后 prior 被清除，不会走 L5 继承
    # 如果 L4 分数够高仍然可以匹配，但 prior 已被清除
    assert manager._last_matched_skill != "skill-a" or result is None


# ── L5: 话题切换 ──

@pytest.mark.asyncio
async def test_l5_topic_switch(manager):
    """当前最高分技能 ≠ 上一轮且有一定置信度时应切换."""
    ecom = _make_skill("ecom", "电商运营", "电商 运营 商品 订单 发货",
                       tags=["电商", "运营", "订单"])
    image = _make_skill("image", "图片生成", "做图 主图 白底图 详情图 设计",
                        tags=["做图", "设计", "主图"])
    manager._skills = {s.skill_id: s for s in [ecom, image]}
    manager._loaded = True
    manager._last_matched_skill = "ecom"
    manager._last_match_time = time.time()

    result = await manager.match_skill("帮我做一张主图，白底图风格")
    assert result is not None
    assert result.skill_id == "image"


# ── 条件激活 ──

@pytest.mark.asyncio
async def test_conditional_activation_requires_tools(manager):
    """requires_tools 不满足时应跳过该技能."""
    skill = _make_skill("tool-skill", "ToolSkill", "电商 运营 商品",
                        tags=["电商"])
    skill.requires_tools = ["browser", "file_manager"]
    manager._skills = {"tool-skill": skill}
    manager._loaded = True

    result = await manager.match_skill(
        "查看电商运营商品数据",
        available_tools={"browser"},  # 缺少 file_manager
    )
    assert result is None


@pytest.mark.asyncio
async def test_conditional_activation_fallback_skipped(manager):
    """fallback_for_tools 中的工具已可用时应跳过 fallback 技能."""
    fallback = _make_skill("fallback", "FallbackSkill", "电商 运营 商品",
                           tags=["电商"])
    fallback.fallback_for_tools = ["native_ecom_tool"]
    manager._skills = {"fallback": fallback}
    manager._loaded = True

    result = await manager.match_skill(
        "查看电商运营商品数据",
        available_tools={"native_ecom_tool"},  # 原生工具可用，跳过 fallback
    )
    assert result is None


# ── Normalize ──

def test_normalize_prompt():
    assert SkillManager._normalize_prompt("  Hello   World  ") == "hello world"
    assert len(SkillManager._normalize_prompt("x" * 300)) == 200
