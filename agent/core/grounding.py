"""Layer 4: Response Grounding Validator — 回复溯源校验.

从工具结果中提取事实 token，检查模型回复是否引用了这些 token。
纯启发式，无 LLM 调用。含监控追踪器。
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path

_CJK_RANGE = r"\u4e00-\u9fff\u3400-\u4dbf"
_CJK_STOP = {"的", "了", "在", "是", "和", "有", "个", "这", "那", "也", "为", "与", "或", "不", "都", "到", "被", "把", "从"}

_RE_NUMBER = re.compile(r"\d[\d,.]*\d|\d+")
_RE_TIME = re.compile(r"\d{1,2}:\d{2}")
_RE_DATE = re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}")
_RE_CJK_SEG = re.compile(rf"[{_CJK_RANGE}]{{2,}}")
_RE_ASCII_WORD = re.compile(r"[a-zA-Z_]{4,}")


def extract_fact_tokens(text: str) -> set[str]:
    """从文本中提取事实性 token（数字、时间、日期、CJK 词段、ASCII 词）."""
    tokens: set[str] = set()
    covered: set[int] = set()

    for m in _RE_TIME.finditer(text):
        tokens.add(m.group())
        covered.update(range(m.start(), m.end()))
    for m in _RE_DATE.finditer(text):
        tokens.add(m.group())
        covered.update(range(m.start(), m.end()))
    for m in _RE_NUMBER.finditer(text):
        if any(i in covered for i in range(m.start(), m.end())):
            continue
        val = m.group()
        if len(val) < 2 or len(val) > 6:
            continue
        tokens.add(val)
    for m in _RE_CJK_SEG.finditer(text):
        seg = m.group()
        if seg not in _CJK_STOP and len(seg) >= 2:
            tokens.add(seg)

    return tokens


def check_grounding(
    tool_results: list[str],
    response_text: str,
    threshold: float = 0.2,
) -> tuple[bool, float]:
    """校验回复是否基于工具结果.

    Returns:
        (grounded, score) — grounded=True 表示通过校验
    """
    combined = "\n".join(tool_results)
    fact_set = extract_fact_tokens(combined)

    if len(fact_set) < 3:
        return True, 1.0

    matched = sum(1 for t in fact_set if t in response_text)
    score = matched / len(fact_set)
    return score >= threshold, score


# ---------------------------------------------------------------------------
# Grounding 监控
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


@dataclass
class GroundingRecord:
    timestamp: float
    user_message: str
    is_factual: bool
    tool_calls_count: int
    grounding_score: float
    grounded: bool
    retried: bool
    hard_blocked: bool

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp,
            "msg": self.user_message[:100],
            "factual": self.is_factual,
            "tools": self.tool_calls_count,
            "score": round(self.grounding_score, 3),
            "grounded": self.grounded,
            "retried": self.retried,
            "blocked": self.hard_blocked,
        }


class GroundingTracker:
    """JSONL 按天记录 grounding 校验结果."""

    def __init__(self, data_dir: Path | None = None) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".xjd-agent" / "grounding"
        self._data_dir = data_dir
        self._records: list[GroundingRecord] = []
        self._threshold: float = 0.2

    @property
    def threshold(self) -> float:
        return self._threshold

    async def initialize(self) -> None:
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self.auto_tune_threshold()

    def record(self, rec: GroundingRecord) -> None:
        self._records.append(rec)
        self._append_to_file(rec)

    def _append_to_file(self, rec: GroundingRecord) -> None:
        date_str = datetime.datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d")
        path = self._data_dir / f"grounding_{date_str}.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("写入 grounding 记录失败: %s", e)

    def get_grounding_metrics(self) -> dict:
        recs = self._records
        if not recs:
            return {"total_checks": 0}
        total = len(recs)
        passed = sum(1 for r in recs if r.grounded)
        retried = sum(1 for r in recs if r.retried)
        blocked = sum(1 for r in recs if r.hard_blocked)
        scores = [r.grounding_score for r in recs if r.grounding_score >= 0]
        return {
            "total_checks": total,
            "pass_rate": round(passed / total, 3),
            "retry_rate": round(retried / total, 3),
            "hard_block_rate": round(blocked / total, 3),
            "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
        }

    def load_history(self, days: int = 7) -> list[GroundingRecord]:
        """从 JSONL 文件加载历史记录."""
        records: list[GroundingRecord] = []
        today = datetime.date.today()
        for i in range(days):
            d = today - datetime.timedelta(days=i)
            path = self._data_dir / f"grounding_{d.isoformat()}.jsonl"
            if not path.exists():
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        obj = json.loads(line)
                        records.append(GroundingRecord(
                            timestamp=obj.get("ts", 0),
                            user_message=obj.get("msg", ""),
                            is_factual=obj.get("factual", True),
                            tool_calls_count=obj.get("tools", 0),
                            grounding_score=obj.get("score", -1),
                            grounded=obj.get("grounded", True),
                            retried=obj.get("retried", False),
                            hard_blocked=obj.get("blocked", False),
                        ))
            except Exception as e:
                logger.warning("读取 grounding 历史失败 (%s): %s", path.name, e)
        return records

    def get_historical_metrics(self, days: int = 7) -> dict:
        """聚合历史 grounding 指标（含按天分组）."""
        recs = self.load_history(days)
        if not recs:
            return {"total_checks": 0, "daily_breakdown": {}}
        total = len(recs)
        passed = sum(1 for r in recs if r.grounded)
        retried = sum(1 for r in recs if r.retried)
        blocked = sum(1 for r in recs if r.hard_blocked)
        scores = [r.grounding_score for r in recs if r.grounding_score >= 0]

        by_day: dict[str, list[GroundingRecord]] = {}
        for r in recs:
            day_key = datetime.datetime.fromtimestamp(r.timestamp).strftime("%Y-%m-%d")
            by_day.setdefault(day_key, []).append(r)

        daily = {}
        for day_key, day_recs in sorted(by_day.items()):
            dt = len(day_recs)
            dp = sum(1 for r in day_recs if r.grounded)
            ds = [r.grounding_score for r in day_recs if r.grounding_score >= 0]
            daily[day_key] = {
                "total": dt,
                "pass_rate": round(dp / dt, 3),
                "avg_score": round(sum(ds) / len(ds), 3) if ds else 0.0,
            }

        return {
            "total_checks": total,
            "pass_rate": round(passed / total, 3),
            "retry_rate": round(retried / total, 3),
            "hard_block_rate": round(blocked / total, 3),
            "avg_score": round(sum(scores) / len(scores), 3) if scores else 0.0,
            "threshold": self._threshold,
            "daily_breakdown": daily,
        }

    def auto_tune_threshold(self, days: int = 7) -> float:
        """根据历史数据自动调优 grounding threshold."""
        recs = self.load_history(days)
        factual = [r for r in recs if r.is_factual and r.grounding_score >= 0]
        if len(factual) < 20:
            return self._threshold

        blocked_scores = sorted(
            r.grounding_score for r in factual if r.hard_blocked
        )
        if not blocked_scores:
            return self._threshold

        mid = len(blocked_scores) // 2
        if len(blocked_scores) % 2 == 0:
            median = (blocked_scores[mid - 1] + blocked_scores[mid]) / 2
        else:
            median = blocked_scores[mid]

        tuned = max(0.15, min(0.5, median))
        self._threshold = round(tuned, 3)
        logger.info("Grounding threshold auto-tuned to %.3f (from %d records)", self._threshold, len(factual))
        return self._threshold
