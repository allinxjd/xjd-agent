"""Tests for Company orchestrator — covers parsing, detection, and file collection logic."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent.company.company import Company
from agent.company.message import CompanyMessage


class _FakeRouter:
    """Minimal router stub for Company.__init__."""
    pass


def _make_company() -> Company:
    return Company(router=_FakeRouter())


# ── _is_review_approved ──────────────────────────────────────

class TestIsReviewApproved:
    def setup_method(self):
        self.c = _make_company()

    def test_approved(self):
        assert self.c._is_review_approved("代码质量不错\nAPPROVED") is True

    def test_rejected(self):
        assert self.c._is_review_approved("问题很多\nREJECTED") is False

    def test_approved_and_rejected_both_present(self):
        assert self.c._is_review_approved("APPROVED but also REJECTED") is False

    def test_not_approved_no_false_positive(self):
        result = self.c._is_review_approved("NOT APPROVED")
        assert result is False

    def test_only_checks_last_lines(self):
        text = "APPROVED in the beginning\n\nbut then\n\nREJECTED at the end"
        assert self.c._is_review_approved(text) is False

    def test_approved_in_last_lines(self):
        text = "some discussion\n\nfinal verdict:\nAPPROVED"
        assert self.c._is_review_approved(text) is True

    def test_empty(self):
        assert self.c._is_review_approved("") is False


# ── _is_test_passed ──────────────────────────────────────────

class TestIsTestPassed:
    def setup_method(self):
        self.c = _make_company()

    def test_pass_indicators(self):
        assert self.c._is_test_passed("全部通过，没有问题") is True
        assert self.c._is_test_passed("测试通过") is True
        assert self.c._is_test_passed("PASS") is True
        assert self.c._is_test_passed("稳了") is True

    def test_fail_indicators(self):
        assert self.c._is_test_passed("3 个测试失败") is False
        assert self.c._is_test_passed("FAIL") is False
        assert self.c._is_test_passed("不通过") is False

    def test_pass_and_fail_both_present(self):
        assert self.c._is_test_passed("测试通过 2 个，失败 1 个") is False

    def test_no_error_not_false_positive(self):
        result = self.c._is_test_passed("测试通过，no error found")
        assert result is True

    def test_real_error(self):
        assert self.c._is_test_passed("测试通过 but ERROR in module X") is False

    def test_empty(self):
        assert self.c._is_test_passed("") is False

    def test_no_indicators(self):
        assert self.c._is_test_passed("代码已经写好了") is False


# ── _detect_task_intent ──────────────────────────────────────

class TestDetectTaskIntent:
    def setup_method(self):
        self.c = _make_company()

    def test_strong_triggers(self):
        assert self.c._detect_task_intent("帮我开发一个商城") is True
        assert self.c._detect_task_intent("开干") is True
        assert self.c._detect_task_intent("启动流水线") is True
        assert self.c._detect_task_intent("写一个爬虫") is True

    def test_weak_triggers(self):
        assert self.c._detect_task_intent("加个按钮") == "weak"
        assert self.c._detect_task_intent("改一下颜色") == "weak"
        assert self.c._detect_task_intent("优化一下性能") == "weak"

    def test_no_trigger(self):
        assert self.c._detect_task_intent("你好") is False
        assert self.c._detect_task_intent("今天天气怎么样") is False
        assert self.c._detect_task_intent("辛苦了") is False

    def test_anti_keywords_block(self):
        assert self.c._detect_task_intent("加油") is False
        assert self.c._detect_task_intent("加班太累了") is False
        assert self.c._detect_task_intent("开发者大会") is False
        assert self.c._detect_task_intent("修改密码") is False

    def test_anti_keyword_overrides_weak(self):
        assert self.c._detect_task_intent("开发环境搭建") is False

    def test_weak_is_truthy(self):
        result = self.c._detect_task_intent("加个按钮")
        assert result  # "weak" is truthy, so pipeline supplement detection still works


# ── _check_rework ────────────────────────────────────────────

class TestCheckRework:
    def setup_method(self):
        self.c = _make_company()

    def test_reviewer_rejected(self):
        assert self.c._check_rework("Reviewer", "代码有问题\nREJECTED") == "Developer"

    def test_reviewer_approved(self):
        assert self.c._check_rework("Reviewer", "APPROVED") is None

    def test_reviewer_chinese_reject(self):
        assert self.c._check_rework("Reviewer", "打回重做") == "Developer"

    def test_qa_fail(self):
        assert self.c._check_rework("QA", "3 个测试失败") == "Developer"

    def test_qa_pass(self):
        assert self.c._check_rework("QA", "全部通过") is None

    def test_developer_no_rework(self):
        assert self.c._check_rework("Developer", "REJECTED") is None

    def test_pm_no_rework(self):
        assert self.c._check_rework("PM", "REJECTED") is None


# ── _collect_project_files ───────────────────────────────────

class TestCollectProjectFiles:
    def test_basic_collection(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            (src / "main.py").write_text("print('hello')")
            (src / "utils.py").write_text("def add(a, b): return a + b")

            result = Company._collect_project_files(p)
            assert "main.py" in result
            assert "utils.py" in result
            assert "print('hello')" in result

    def test_skips_binary_extensions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            (src / "main.py").write_text("code")
            (src / "data.pyc").write_bytes(b"\x00\x01\x02")
            (src / "image.png").write_bytes(b"\x89PNG")

            result = Company._collect_project_files(p)
            assert "main.py" in result
            assert "data.pyc" not in result
            assert "image.png" not in result

    def test_skips_dotfiles(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            (src / "app.py").write_text("code")
            (src / ".hidden").write_text("secret")

            result = Company._collect_project_files(p)
            assert "app.py" in result
            assert ".hidden" not in result

    def test_truncation_at_max_chars(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            for i in range(50):
                (src / f"file_{i:03d}.py").write_text("x" * 1000)

            result = Company._collect_project_files(p, max_chars=5000)
            assert len(result) <= 6000  # some overhead from headers
            assert "省略" in result or "截断" in result

    def test_no_src_falls_back_to_root(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "main.py").write_text("code")

            result = Company._collect_project_files(p)
            assert "main.py" in result

    def test_empty_project(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "src").mkdir()

            result = Company._collect_project_files(p)
            assert result == ""

    def test_max_files_limit(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            for i in range(20):
                (src / f"file_{i:03d}.py").write_text(f"# file {i}")

            result = Company._collect_project_files(p, max_files=5)
            assert "文件上限" in result

    def test_skips_binary_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            (src / "good.py").write_text("code")
            (src / "binary.dat").write_bytes(b"header\x00binary\x00data")

            result = Company._collect_project_files(p)
            assert "good.py" in result
            assert "binary.dat" not in result

    def test_skips_large_files(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            src = p / "src"
            src.mkdir()
            (src / "small.py").write_text("code")
            (src / "huge.py").write_text("x" * 60000)

            result = Company._collect_project_files(p)
            assert "small.py" in result
            assert "huge.py" not in result


# ── _has_requirement_issue ───────────────────────────────────

class TestHasRequirementIssue:
    def setup_method(self):
        self.c = _make_company()

    def test_detects_issues(self):
        assert self.c._has_requirement_issue("需求不清，无法继续") is True
        assert self.c._has_requirement_issue("需求为空，请补充") is True
        assert self.c._has_requirement_issue("安全轮次上限已到") is True

    def test_normal_content(self):
        assert self.c._has_requirement_issue("PRD 已完成，包含以下功能...") is False


# ── _has_code_incomplete ─────────────────────────────────────

class TestHasCodeIncomplete:
    def setup_method(self):
        self.c = _make_company()

    def test_detects_incomplete(self):
        assert self.c._has_code_incomplete("没有 diff 可审查") is True
        assert self.c._has_code_incomplete("交白卷了") is True

    def test_normal_review(self):
        assert self.c._has_code_incomplete("代码质量不错，APPROVED") is False


# ── _is_no_work ──────────────────────────────────────────────

class TestIsNoWork:
    def setup_method(self):
        self.c = _make_company()

    def test_detects_no_work(self):
        assert self.c._is_no_work("没有需求") is True
        assert self.c._is_no_work("部署完成（无需操作）") is True

    def test_long_content_not_no_work(self):
        assert self.c._is_no_work("没有需求" + "x" * 500) is False

    def test_normal_output(self):
        assert self.c._is_no_work("PRD 文档如下...") is False


# ── _detect_quick_task ───────────────────────────────────────

class TestDetectQuickTask:
    def setup_method(self):
        self.c = _make_company()
        self.c._standby_history = []

    def test_devops_keywords(self):
        msgs = [CompanyMessage(content="启动项目", sent_from="Human")]
        assert self.c._detect_quick_task(msgs) == "DevOps"

    def test_developer_keywords(self):
        msgs = [CompanyMessage(content="查日志看看", sent_from="Human")]
        assert self.c._detect_quick_task(msgs) == "Developer"

    def test_qa_keywords(self):
        msgs = [CompanyMessage(content="跑测试", sent_from="Human")]
        assert self.c._detect_quick_task(msgs) == "QA"

    def test_no_match(self):
        msgs = [CompanyMessage(content="你好", sent_from="Human")]
        assert self.c._detect_quick_task(msgs) is None


# ── _extract_project_name ────────────────────────────────────

class TestExtractProjectName:
    def test_simple(self):
        result = Company._extract_project_name("商城系统")
        assert "商城" in result

    def test_strips_prefix(self):
        result = Company._extract_project_name("做一个商城")
        assert "商城" in result
        assert "做一个" not in result

    def test_strips_pm_preamble(self):
        result = Company._extract_project_name("老板，核心需求是：做一个聊天工具")
        assert len(result) <= 10

    def test_max_length(self):
        result = Company._extract_project_name("一个非常非常非常非常非常长的项目名称描述")
        assert len(result) <= 10

    def test_stopword_filtered(self):
        result = Company._extract_project_name("是的，你接着开发Holu项目")
        assert result != "是"
        assert len(result) >= 2

    def test_single_char_filtered(self):
        result = Company._extract_project_name("是")
        assert result != "是"

    def test_continuation_prefix_stripped(self):
        result = Company._extract_project_name("是的，接着开发Holu")
        assert "是" not in result or "Holu" in result

    def test_empty_returns_project(self):
        result = Company._extract_project_name("")
        assert result == "project"


# ── _route_message_to_role ───────────────────────────────────

class TestRouteMessageToRole:
    def setup_method(self):
        self.c = _make_company()
        from agent.company.role import CompanyRole
        for name in ["PM", "Developer", "Reviewer", "QA", "DevOps"]:
            role = CompanyRole(name=name, description=f"{name} role", system_prompt="")
            self.c._env._roles[name] = role

    def test_explicit_send_to(self):
        msg = CompanyMessage(content="hello", send_to="Developer")
        assert self.c._route_message_to_role(msg) == "Developer"

    def test_nick_match(self):
        msg = CompanyMessage(content="让运维看看")
        assert self.c._route_message_to_role(msg) == "DevOps"

    def test_topic_match(self):
        msg = CompanyMessage(content="服务器挂了")
        assert self.c._route_message_to_role(msg) == "DevOps"

    def test_default_none(self):
        msg = CompanyMessage(content="你好")
        assert self.c._route_message_to_role(msg) is None


# ── PipelineConfig ──────────────────────────────────────────

class TestPipelineConfig:
    def test_default_role_order(self):
        from agent.company.company import PipelineConfig
        pc = PipelineConfig.default()
        assert pc.role_order == ["PM", "Developer", "Reviewer", "QA", "DevOps"]

    def test_default_stage_keys(self):
        from agent.company.company import PipelineConfig
        pc = PipelineConfig.default()
        assert "PRD" in pc.stage_keys
        assert "Code" in pc.stage_keys
        assert "Deploy" in pc.stage_keys

    def test_rework_target_for(self):
        from agent.company.company import PipelineConfig
        pc = PipelineConfig.default()
        assert pc.rework_target_for("Reviewer") == "Developer"
        assert pc.rework_target_for("PM") == ""
        assert pc.rework_target_for("Developer") == ""

    def test_from_workflow(self):
        from agent.company.company import PipelineConfig
        wf = {
            "steps": [
                {"role": "Developer", "action": "WriteCode", "stage_key": "Code"},
                {"role": "Reviewer", "action": "CodeReview", "stage_key": "Review", "rework_target": "Developer"},
                {"role": "QA", "action": "RunTest", "stage_key": "Test"},
            ]
        }
        pc = PipelineConfig.from_workflow(wf)
        assert pc.role_order == ["Developer", "Reviewer", "QA"]
        assert pc.rework_target_for("Reviewer") == "Developer"
        assert pc.rework_target_for("QA") == ""

    def test_from_workflow_empty_falls_back(self):
        from agent.company.company import PipelineConfig
        pc = PipelineConfig.from_workflow({"steps": []})
        assert pc.role_order == PipelineConfig.default().role_order

    def test_custom_pipeline_check_rework(self):
        from agent.company.company import CompanyConfig, PipelineConfig, PipelineStage
        pc = PipelineConfig(stages=[
            PipelineStage(role="Developer", action="WriteCode", stage_key="Code"),
            PipelineStage(role="QA", action="RunTest", stage_key="Test", rework_target="Developer"),
        ])
        cfg = CompanyConfig(pipeline=pc)
        c = Company(router=_FakeRouter(), config=cfg)
        assert c._check_rework("QA", "3 个测试失败") == "Developer"
        assert c._check_rework("Reviewer", "REJECTED") is None
