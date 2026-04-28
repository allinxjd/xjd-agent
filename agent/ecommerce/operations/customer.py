"""客服管理操作 — 自动回复/评价管理/消息模板.

平台适配器实现具体的页面操作，本模块提供跨平台的通用逻辑:
- 自动回复规则
- 评价回复模板
- 消息分类与优先级
"""

from __future__ import annotations

import asyncio
import heapq
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MessageCategory(IntEnum):
    GENERAL = 0
    PRE_SALE = 1
    POST_SALE = 2
    LOGISTICS = 3
    COMPLAINT = 4


_CATEGORY_KEYWORDS: dict[MessageCategory, set[str]] = {
    MessageCategory.COMPLAINT: {
        "投诉", "举报", "315", "工商", "差评", "骗", "假货", "欺诈",
        "消协", "曝光", "垃圾", "骗子",
    },
    MessageCategory.LOGISTICS: {
        "物流", "快递", "发货", "到货", "签收", "运费", "配送",
        "单号", "快递单", "几天到", "什么时候发", "还没到",
    },
    MessageCategory.POST_SALE: {
        "退货", "退款", "换货", "售后", "质量问题", "破损", "坏了",
        "不合适", "退回", "退钱", "维修", "保修",
    },
    MessageCategory.PRE_SALE: {
        "多少钱", "价格", "尺码", "颜色", "规格", "有货", "库存",
        "优惠", "折扣", "包邮", "材质", "怎么用", "适合",
    },
}


class MessageClassifier:
    """消息意图分类器 — 关键词快速匹配."""

    @staticmethod
    def classify(text: str) -> MessageCategory:
        lower = text.lower()
        for cat in (MessageCategory.COMPLAINT, MessageCategory.LOGISTICS,
                    MessageCategory.POST_SALE, MessageCategory.PRE_SALE):
            if any(kw in lower for kw in _CATEGORY_KEYWORDS[cat]):
                return cat
        return MessageCategory.GENERAL


@dataclass(order=True)
class PrioritizedMessage:
    priority: int
    timestamp: float = field(compare=False)
    uid: str = field(compare=False)
    content: str = field(compare=False)
    category: MessageCategory = field(compare=False)
    goods_info: Optional[dict] = field(default=None, compare=False)
    order_info: Optional[dict] = field(default=None, compare=False)


_PRIORITY_MAP: dict[MessageCategory, int] = {
    MessageCategory.COMPLAINT: 0,
    MessageCategory.POST_SALE: 1,
    MessageCategory.LOGISTICS: 2,
    MessageCategory.PRE_SALE: 3,
    MessageCategory.GENERAL: 4,
}


class PriorityQueue:
    """优先级消息队列 — 投诉 > 售后 > 物流 > 售前 > 通用."""

    def __init__(self) -> None:
        self._heap: list[PrioritizedMessage] = []

    def enqueue(self, msg: PrioritizedMessage) -> None:
        heapq.heappush(self._heap, msg)

    def dequeue(self) -> Optional[PrioritizedMessage]:
        if self._heap:
            return heapq.heappop(self._heap)
        return None

    @property
    def size(self) -> int:
        return len(self._heap)

    def is_empty(self) -> bool:
        return len(self._heap) == 0


class ConversationContext:
    """按买家 uid 维护最近对话历史，支持过期清理."""

    def __init__(self, max_turns: int = 10, expire_minutes: int = 30) -> None:
        self._max_turns = max_turns
        self._expire_seconds = expire_minutes * 60
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._last_active: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def add_message(self, uid: str, role: str, content: str) -> None:
        async with self._lock:
            now = time.time()
            self._last_active[uid] = now
            history = self._history[uid]
            history.append({"role": role, "content": content, "ts": now})
            if len(history) > self._max_turns * 2:
                self._history[uid] = history[-self._max_turns * 2:]

    async def get_history(self, uid: str) -> list[dict]:
        async with self._lock:
            self._evict_if_expired(uid)
            return [{"role": m["role"], "content": m["content"]} for m in self._history.get(uid, [])]

    def _evict_if_expired(self, uid: str) -> None:
        last = self._last_active.get(uid, 0)
        if last and time.time() - last > self._expire_seconds:
            self._history.pop(uid, None)
            self._last_active.pop(uid, None)

    def clear_all(self) -> None:
        self._history.clear()
        self._last_active.clear()


@dataclass
class BusinessHoursConfig:
    start_hour: int = 9
    end_hour: int = 22
    timezone: str = "Asia/Shanghai"
    outside_hours_reply: str = "当前非工作时间，我们将在工作时间尽快回复您。"

    def is_business_hours(self) -> bool:
        from datetime import datetime
        try:
            import zoneinfo
            tz = zoneinfo.ZoneInfo(self.timezone)
        except Exception:
            tz = None
        now = datetime.now(tz)
        return self.start_hour <= now.hour < self.end_hour


class KnowledgeBase:
    """双知识库 — 产品知识 + 客服 FAQ."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        self._product_cache: dict[str, dict] = {}
        self._faq: list[dict] = []
        self._transfer_keywords: set[str] = {"人工", "投诉", "315", "工商", "转人工"}
        self._templates: dict[str, str] = {}
        self._system_prompt: str = ""
        self._business_hours = BusinessHoursConfig()
        if config_path and config_path.exists():
            self._load_config(config_path)

    def _load_config(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._faq = data.get("faq", [])
            kws = data.get("transfer_keywords", [])
            if kws:
                self._transfer_keywords = set(kws)
            self._templates = data.get("templates", {})
            self._system_prompt = data.get("system_prompt", "")
            bh = data.get("business_hours", {})
            if bh:
                self._business_hours = BusinessHoursConfig(
                    start_hour=bh.get("start", 9),
                    end_hour=bh.get("end", 22),
                    timezone=bh.get("timezone", "Asia/Shanghai"),
                    outside_hours_reply=self._templates.get("outside_hours", self._business_hours.outside_hours_reply),
                )
        except Exception as e:
            logger.warning("加载客服知识库配置失败: %s", e)

    def cache_product(self, product_id: str, info: dict) -> None:
        self._product_cache[product_id] = info

    def search(self, query: str) -> list[str]:
        results: list[str] = []
        lower = query.lower()
        query_grams = {lower[i:i+2] for i in range(len(lower)-1)} if len(lower) >= 2 else {lower}
        for faq in self._faq:
            q = faq.get("q", "").lower()
            q_grams = {q[i:i+2] for i in range(len(q)-1)} if len(q) >= 2 else {q}
            overlap = len(q_grams & query_grams)
            if overlap >= 2 or lower in q or q in lower:
                results.append(f"Q: {faq['q']}\nA: {faq['a']}")
        for pid, info in self._product_cache.items():
            name = str(info.get("title", info.get("goods_name", ""))).lower()
            name_grams = {name[i:i+2] for i in range(len(name)-1)} if len(name) >= 2 else {name}
            if len(name_grams & query_grams) >= 2 or lower in name:
                results.append(f"商品: {info.get('title', pid)} — ¥{info.get('price', '?')}")
        return results[:5]

    def should_transfer(self, text: str) -> bool:
        return any(kw in text for kw in self._transfer_keywords)

    @property
    def business_hours(self) -> BusinessHoursConfig:
        return self._business_hours

    @property
    def templates(self) -> dict[str, str]:
        return self._templates

    @property
    def system_prompt(self) -> str:
        return self._system_prompt


class AutoReplyEngine:
    """智能客服自动回复引擎.

    流程: 分类 → 检查转人工 → 搜索知识库 → 构建上下文 → LLM 生成回复
    """

    def __init__(
        self,
        kb: KnowledgeBase,
        llm_fn: Optional[Any] = None,
        context: Optional[ConversationContext] = None,
    ) -> None:
        self._kb = kb
        self._llm_fn = llm_fn
        self._context = context or ConversationContext()
        self._classifier = MessageClassifier()
        self._stats = {"total": 0, "auto_replied": 0, "transferred": 0, "llm_replied": 0}

    @property
    def stats(self) -> dict[str, int]:
        return dict(self._stats)

    @property
    def conversation_context(self) -> ConversationContext:
        return self._context

    async def handle_message(
        self,
        uid: str,
        content: str,
        goods_info: Optional[dict] = None,
        order_info: Optional[dict] = None,
    ) -> dict[str, Any]:
        self._stats["total"] += 1
        category = self._classifier.classify(content)
        await self._context.add_message(uid, "buyer", content)

        if self._kb.should_transfer(content):
            self._stats["transferred"] += 1
            reply = self._kb.templates.get("transfer", "正在为您转接人工客服，请稍候...")
            await self._context.add_message(uid, "cs", reply)
            return {"action": "transfer", "reply": reply, "category": category.name, "transfer": True}

        kb_results = self._kb.search(content)
        if goods_info:
            kb_results.extend(self._kb.search(goods_info.get("goods_name", "")))

        if self._llm_fn:
            try:
                history = await self._context.get_history(uid)
                kb_text = "\n".join(kb_results[:3]) if kb_results else ""
                reply = await self._llm_fn(
                    history=history, kb_context=kb_text, category=category.name,
                    goods_info=goods_info, order_info=order_info,
                )
                if reply:
                    self._stats["auto_replied"] += 1
                    self._stats["llm_replied"] += 1
                    await self._context.add_message(uid, "cs", reply)
                    return {
                        "action": "auto_reply", "reply": reply, "category": category.name,
                        "transfer": False, "kb_hits": len(kb_results), "source": "llm",
                    }
            except Exception as e:
                logger.warning("LLM 回复生成失败: %s", e)

        if kb_results:
            self._stats["auto_replied"] += 1
            reply = kb_results[0].split("\nA: ")[-1] if "\nA: " in kb_results[0] else kb_results[0]
            await self._context.add_message(uid, "cs", reply)
            return {
                "action": "auto_reply", "reply": reply, "category": category.name,
                "transfer": False, "kb_hits": len(kb_results), "source": "kb",
            }

        greeting = self._kb.templates.get("greeting", "您好，请问有什么可以帮您？")
        await self._context.add_message(uid, "cs", greeting)
        return {"action": "fallback", "reply": greeting, "category": category.name, "transfer": False, "source": "template"}
