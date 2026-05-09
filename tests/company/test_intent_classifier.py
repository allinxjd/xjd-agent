"""Tests for IntentClassifier."""

import pytest
from agent.company.intent_classifier import IntentClassifier, ClassificationResult


@pytest.fixture
def classifier():
    return IntentClassifier(router=None, locale=None)


class TestClassifySync:
    """关键词分类测试."""

    def test_question_chinese(self, classifier):
        result = classifier.classify_sync("开发完成了吗？")
        assert result.intent == "question"

    def test_question_progress(self, classifier):
        result = classifier.classify_sync("进度如何")
        assert result.intent == "question"

    def test_question_english(self, classifier):
        result = classifier.classify_sync("is it done?")
        assert result.intent == "question"

    def test_confirm_exact(self, classifier):
        result = classifier.classify_sync("确认")
        assert result.intent == "confirm"

    def test_confirm_ok(self, classifier):
        result = classifier.classify_sync("OK")
        assert result.intent == "confirm"

    def test_confirm_no_problem(self, classifier):
        result = classifier.classify_sync("没问题")
        assert result.intent == "confirm"

    def test_cancel_pause(self, classifier):
        result = classifier.classify_sync("暂停")
        assert result.intent == "cancel"

    def test_cancel_stop(self, classifier):
        result = classifier.classify_sync("先停一下")
        assert result.intent == "cancel"

    def test_new_task_strong(self, classifier):
        result = classifier.classify_sync("帮我开发一个登录页面")
        assert result.intent == "new_task"
        assert result.confidence >= 0.8

    def test_new_task_weak(self, classifier):
        result = classifier.classify_sync("加个按钮")
        assert result.intent == "new_task"
        assert result.confidence < 0.8

    def test_supplement_default(self, classifier):
        result = classifier.classify_sync("颜色用蓝色的")
        assert result.intent == "supplement"

    def test_anti_keyword_blocks_task(self, classifier):
        result = classifier.classify_sync("开发者文档在哪里")
        assert result.intent != "new_task"

    def test_empty_string(self, classifier):
        result = classifier.classify_sync("")
        assert result.intent == "supplement"
        assert result.confidence == 0.0

    def test_question_does_not_trigger_task(self, classifier):
        """问句中包含任务关键词也不应触发 new_task."""
        result = classifier.classify_sync("全部开发完成了吗？")
        assert result.intent == "question"

    def test_confirm_short_message(self, classifier):
        result = classifier.classify_sync("好的，继续")
        assert result.intent == "confirm"


class TestCache:
    """缓存测试."""

    def test_cache_hit(self, classifier):
        r1 = classifier.classify_sync("帮我写一个API")
        classifier._put_cache("帮我写一个API", r1)
        r2 = classifier._get_cached("帮我写一个API")
        assert r2 is not None
        assert r2.intent == r1.intent

    def test_cache_miss(self, classifier):
        assert classifier._get_cached("never seen") is None


class TestClassifyAsync:
    """异步分类测试（无 router 降级为关键词）."""

    @pytest.mark.asyncio
    async def test_fallback_to_keywords(self, classifier):
        result = await classifier.classify("帮我开发一个APP")
        assert result.intent == "new_task"
        assert result.source == "keyword"

    @pytest.mark.asyncio
    async def test_question_async(self, classifier):
        result = await classifier.classify("做完了吗？")
        assert result.intent == "question"
