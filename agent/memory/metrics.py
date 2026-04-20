"""记忆系统可观测性 — structured logging + metrics 收集."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryMetrics:
    """记忆系统运行时指标."""

    # 操作计数
    store_count: int = 0
    store_dedup_count: int = 0
    recall_count: int = 0
    feedback_positive: int = 0
    feedback_negative: int = 0
    decay_deleted: int = 0
    decay_downgraded: int = 0

    # 合并
    consolidation_runs: int = 0
    consolidation_merged: int = 0
    consolidation_failures: int = 0

    # 反思
    reflection_runs: int = 0
    reflection_insights: int = 0
    reflection_failures: int = 0

    # 提取
    extraction_runs: int = 0
    extraction_memories: int = 0
    extraction_failures: int = 0

    # LLM 调用
    llm_calls: int = 0
    llm_retries: int = 0
    llm_failures: int = 0

    # 延迟 (ms)
    _latencies: dict[str, list[float]] = field(default_factory=dict)

    def record_latency(self, operation: str, duration_ms: float) -> None:
        """记录操作延迟."""
        self._latencies.setdefault(operation, [])
        bucket = self._latencies[operation]
        bucket.append(duration_ms)
        # 只保留最近 100 个
        if len(bucket) > 100:
            self._latencies[operation] = bucket[-100:]

    def get_avg_latency(self, operation: str) -> float:
        """获取平均延迟."""
        bucket = self._latencies.get(operation, [])
        return sum(bucket) / len(bucket) if bucket else 0.0

    def to_prometheus(self) -> str:
        """输出 Prometheus 格式指标."""
        lines = [
            "# HELP memory_store_total Total memory store operations",
            "# TYPE memory_store_total counter",
            f"memory_store_total {self.store_count}",
            f'memory_store_total{{result="dedup"}} {self.store_dedup_count}',
            "",
            "# HELP memory_recall_total Total memory recall operations",
            "# TYPE memory_recall_total counter",
            f"memory_recall_total {self.recall_count}",
            "",
            "# HELP memory_feedback_total Total feedback signals",
            "# TYPE memory_feedback_total counter",
            f'memory_feedback_total{{signal="positive"}} {self.feedback_positive}',
            f'memory_feedback_total{{signal="negative"}} {self.feedback_negative}',
            "",
            "# HELP memory_decay_total Total decay operations",
            "# TYPE memory_decay_total counter",
            f'memory_decay_total{{action="deleted"}} {self.decay_deleted}',
            f'memory_decay_total{{action="downgraded"}} {self.decay_downgraded}',
            "",
            "# HELP memory_consolidation_total Consolidation operations",
            "# TYPE memory_consolidation_total counter",
            f'memory_consolidation_total{{result="success"}} {self.consolidation_runs}',
            f'memory_consolidation_total{{result="merged"}} {self.consolidation_merged}',
            f'memory_consolidation_total{{result="failure"}} {self.consolidation_failures}',
            "",
            "# HELP memory_reflection_total Reflection operations",
            "# TYPE memory_reflection_total counter",
            f'memory_reflection_total{{result="success"}} {self.reflection_runs}',
            f'memory_reflection_total{{result="insights"}} {self.reflection_insights}',
            f'memory_reflection_total{{result="failure"}} {self.reflection_failures}',
            "",
            "# HELP memory_extraction_total Extraction operations",
            "# TYPE memory_extraction_total counter",
            f'memory_extraction_total{{result="success"}} {self.extraction_runs}',
            f'memory_extraction_total{{result="memories"}} {self.extraction_memories}',
            f'memory_extraction_total{{result="failure"}} {self.extraction_failures}',
            "",
            "# HELP memory_llm_calls_total LLM call statistics",
            "# TYPE memory_llm_calls_total counter",
            f"memory_llm_calls_total {self.llm_calls}",
            f'memory_llm_calls_total{{result="retry"}} {self.llm_retries}',
            f'memory_llm_calls_total{{result="failure"}} {self.llm_failures}',
            "",
        ]

        # 延迟
        for op, bucket in self._latencies.items():
            if bucket:
                avg = sum(bucket) / len(bucket)
                lines.append(f"# HELP memory_latency_ms_{op} Average latency in ms")
                lines.append(f"# TYPE memory_latency_ms_{op} gauge")
                lines.append(f"memory_latency_ms_{op} {avg:.1f}")
                lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """输出为 dict (用于 JSON API)."""
        return {
            "store": {"total": self.store_count, "dedup": self.store_dedup_count},
            "recall": {"total": self.recall_count},
            "feedback": {"positive": self.feedback_positive, "negative": self.feedback_negative},
            "decay": {"deleted": self.decay_deleted, "downgraded": self.decay_downgraded},
            "consolidation": {"runs": self.consolidation_runs, "merged": self.consolidation_merged, "failures": self.consolidation_failures},
            "reflection": {"runs": self.reflection_runs, "insights": self.reflection_insights, "failures": self.reflection_failures},
            "extraction": {"runs": self.extraction_runs, "memories": self.extraction_memories, "failures": self.extraction_failures},
            "llm": {"calls": self.llm_calls, "retries": self.llm_retries, "failures": self.llm_failures},
            "latencies": {op: {"avg_ms": self.get_avg_latency(op), "count": len(b)} for op, b in self._latencies.items()},
        }


# 全局单例
_metrics = MemoryMetrics()


def get_metrics() -> MemoryMetrics:
    """获取全局 metrics 实例."""
    return _metrics


def reset_metrics() -> None:
    """重置 (测试用)."""
    global _metrics
    _metrics = MemoryMetrics()
