"""IntentClassifier — LLM 意图分类 + 关键词降级.

将用户消息分类为: new_task / supplement / question / confirm / cancel
优先使用 LLM 分类（cheap model），失败时降级为关键词匹配。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from agent.company.locale import CompanyLocale
    from agent.core.model_router import ModelRouter

logger = logging.getLogger(__name__)

VALID_INTENTS = ("new_task", "supplement", "question", "confirm", "cancel")

_CLASSIFICATION_PROMPT = """\
You are a message intent classifier for an AI development team pipeline.
Classify the user's message into exactly ONE category:

- new_task: User wants to start a new development task (e.g., "帮我开发一个XX", "写一个XX")
- supplement: User is providing additional info for the current task (e.g., "颜色用蓝色", "参考这个链接")
- question: User is asking a question, not giving instructions (e.g., "做完了吗？", "进度如何？")
- confirm: User is approving/confirming something (e.g., "确认", "没问题", "可以", "OK")
- cancel: User wants to stop/pause the current work (e.g., "暂停", "先停", "取消")

Pipeline state: {state}

User message: {message}

Reply with ONLY the category name, nothing else."""


@dataclass
class ClassificationResult:
    intent: str
    confidence: float
    source: str = "keyword"

    def __post_init__(self) -> None:
        if self.intent not in VALID_INTENTS:
            self.intent = "supplement"


@dataclass
class _CacheEntry:
    result: ClassificationResult
    expires: float


class IntentClassifier:
    """LLM 意图分类器，带关键词降级和 LRU 缓存."""

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        locale: Optional[CompanyLocale] = None,
        cache_size: int = 64,
        cache_ttl: float = 300.0,
    ) -> None:
        self._router = router
        self._locale = locale
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_size = cache_size
        self._cache_ttl = cache_ttl

    # ─── Public API ───────────────────────────────────────────────

    async def classify(
        self, text: str, pipeline_state: str = "idle"
    ) -> ClassificationResult:
        """分类用户消息意图。优先 LLM，失败降级关键词."""
        text = text.strip()
        if not text:
            return ClassificationResult(intent="supplement", confidence=0.0)

        cached = self._get_cached(text)
        if cached:
            return cached

        if self._router:
            try:
                result = await self._classify_llm(text, pipeline_state)
                self._put_cache(text, result)
                return result
            except Exception as e:
                logger.debug("LLM 分类失败，降级关键词: %s", e)

        result = self.classify_sync(text)
        self._put_cache(text, result)
        return result

    def classify_sync(self, text: str) -> ClassificationResult:
        """同步关键词分类（无 LLM 调用）."""
        text = text.strip()
        if not text:
            return ClassificationResult(intent="supplement", confidence=0.0)

        if self._is_cancel(text):
            return ClassificationResult(intent="cancel", confidence=0.9, source="keyword")
        if self._is_confirm(text):
            return ClassificationResult(intent="confirm", confidence=0.9, source="keyword")
        if self._is_question(text):
            return ClassificationResult(intent="question", confidence=0.8, source="keyword")

        task_intent = self._detect_task_keywords(text)
        if task_intent is True:
            return ClassificationResult(intent="new_task", confidence=0.9, source="keyword")
        if task_intent == "weak":
            return ClassificationResult(intent="new_task", confidence=0.5, source="keyword")

        return ClassificationResult(intent="supplement", confidence=0.6, source="keyword")

    # ─── LLM Classification ──────────────────────────────────────

    async def _classify_llm(
        self, text: str, pipeline_state: str
    ) -> ClassificationResult:
        """调用 cheap model 做意图分类."""
        prompt = _CLASSIFICATION_PROMPT.format(
            state=pipeline_state or "idle",
            message=text[:500],
        )
        messages = [{"role": "user", "content": prompt}]
        resp = await self._router.complete_with_failover(
            messages=messages, user_message="classify_intent"
        )
        raw = resp.content.strip().lower().replace('"', "").replace("'", "")
        for intent in VALID_INTENTS:
            if intent in raw:
                return ClassificationResult(
                    intent=intent, confidence=0.85, source="llm"
                )
        return ClassificationResult(intent="supplement", confidence=0.5, source="llm")

    # ─── Keyword Matchers ─────────────────────────────────────────

    def _is_cancel(self, text: str) -> bool:
        cancel_keywords = self._get_locale_list("keywords.cancel_triggers") or [
            "暂停", "先停", "停一下", "暂停开发", "先暂停",
            "取消", "算了", "不做了", "不用了", "停止",
            "cancel", "stop", "abort", "pause",
        ]
        return any(kw in text for kw in cancel_keywords)

    def _is_confirm(self, text: str) -> bool:
        text_clean = text.strip().lower()
        exact_confirms = {
            "确认", "没问题", "可以", "好的", "行", "ok", "yes",
            "通过", "approved", "lgtm", "同意", "确定",
            "开干", "动手吧", "开始吧", "继续",
        }
        if text_clean in exact_confirms:
            return True
        if len(text_clean) <= 15:
            short_confirms = [
                "没问题", "可以", "好的", "行吧", "ok", "确认",
                "通过", "就这样", "开始", "继续", "go",
            ]
            return any(kw in text_clean for kw in short_confirms)
        return False

    def _is_question(self, text: str) -> bool:
        question_signals = [
            "吗？", "吗?", "吗 ", "呢？", "呢?", "了吗",
            "没有？", "没有?", "怎么样", "如何", "什么时候",
            "多久", "进度", "状态", "完成了", "好了吗",
            "?", "？",
        ]
        if any(s in text for s in question_signals):
            return True
        if text.endswith("?") or text.endswith("？"):
            return True
        return False

    def _detect_task_keywords(self, text: str) -> bool | str:
        """关键词任务检测（从 company._detect_task_intent 迁移）."""
        anti_keywords = self._get_locale_list("keywords.task_anti_keywords") or [
            "加油", "加班", "加薪", "加入群", "加入团队",
            "增加信心", "修改密码", "修改头像",
            "开发者大会", "开发者文档", "开发环境", "开发工具",
        ]
        if any(kw in text for kw in anti_keywords):
            return False

        strong = self._get_locale_list("keywords.strong_task_triggers") or self._get_locale_list("keywords.task_triggers") or [
            "开发一个", "写一个", "做一个", "帮我开发", "帮我写", "帮我做",
            "开始开发", "开始写", "开始做", "开干", "开搞", "启动流水线", "开始干活",
            "写个", "做个", "搞一个", "搞个", "实现一个", "实现个",
            "写PRD", "写 PRD", "出PRD", "出 PRD",
            "马上开发", "立刻开发", "赶紧开发", "直接开发",
            "马上做", "赶紧做", "赶紧搞", "快做", "快搞",
            "创建一个", "建一个", "生成一个",
            "安排开发", "安排一下", "动手吧", "动手做", "你就开始",
            "现在就做", "现在就开发", "现在开始",
            "开发好", "做好", "搞好", "实现好",
        ]
        if any(kw in text for kw in strong):
            return True

        weak = self._get_locale_list("keywords.weak_task_triggers") or [
            "加入", "加个", "加一个", "增加", "添加", "新增",
            "改一下", "改个", "修改", "优化一下", "优化个",
            "重构", "翻新", "改版", "升级", "迭代",
            "支持一下", "支持个", "接入",
            "开发", "开发个",
        ]
        if any(kw in text for kw in weak):
            return "weak"

        return False

    # ─── Helpers ──────────────────────────────────────────────────

    def _get_locale_list(self, key: str) -> Optional[list[str]]:
        if self._locale:
            return self._locale.get(key)
        return None

    def _get_cached(self, text: str) -> Optional[ClassificationResult]:
        key = text[:200]
        entry = self._cache.get(key)
        if entry and time.time() < entry.expires:
            self._cache.move_to_end(key)
            return entry.result
        if entry:
            del self._cache[key]
        return None

    def _put_cache(self, text: str, result: ClassificationResult) -> None:
        key = text[:200]
        self._cache[key] = _CacheEntry(
            result=result, expires=time.time() + self._cache_ttl
        )
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
