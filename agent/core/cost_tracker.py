"""成本追踪器 — 模型调用成本统计与预算管理.

追踪每次 API 调用的 token 用量和费用，支持:
- 按 provider/model 分类统计
- 按时间段查询
- 预算告警
- 导出报告
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

@dataclass
class UsageRecord:
    """单次调用记录."""

    timestamp: float = 0.0
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    route: str = ""  # "cheap" | "strong"
    success: bool = True

    def to_dict(self) -> dict:
        return {
            "ts": self.timestamp, "provider": self.provider,
            "model": self.model, "in": self.input_tokens,
            "out": self.output_tokens, "cost": self.total_cost,
            "route": self.route, "ok": self.success,
        }

@dataclass
class CostSummary:
    """成本汇总."""

    total_cost: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    by_provider: dict[str, float] = field(default_factory=dict)
    by_model: dict[str, float] = field(default_factory=dict)
    by_route: dict[str, float] = field(default_factory=dict)

class CostTracker:
    """成本追踪器 — 记录并统计模型调用费用."""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        budget_limit: float = 0.0,
        on_budget_alert: Optional[Callable] = None,
    ) -> None:
        if data_dir is None:
            data_dir = Path.home() / ".xjd-agent" / "costs"
        self._data_dir = Path(data_dir)
        self._budget_limit = budget_limit
        self._on_budget_alert = on_budget_alert
        self._session_records: list[UsageRecord] = []
        self._session_cost: float = 0.0
        self._initialized = False

    async def initialize(self) -> None:
        """初始化存储目录."""
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._initialized = True

    def record(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        input_price_per_mtok: float = 0.0,
        output_price_per_mtok: float = 0.0,
        route: str = "strong",
        success: bool = True,
    ) -> UsageRecord:
        """记录一次 API 调用."""
        input_cost = input_tokens * input_price_per_mtok / 1_000_000
        output_cost = output_tokens * output_price_per_mtok / 1_000_000

        rec = UsageRecord(
            timestamp=time.time(),
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            input_cost=input_cost,
            output_cost=output_cost,
            total_cost=input_cost + output_cost,
            route=route,
            success=success,
        )

        self._session_records.append(rec)
        self._session_cost += rec.total_cost

        # 持久化
        self._append_to_file(rec)

        # 预算告警
        if self._budget_limit > 0 and self._session_cost >= self._budget_limit:
            if self._on_budget_alert:
                self._on_budget_alert(self._session_cost, self._budget_limit)

        return rec

    def _append_to_file(self, rec: UsageRecord) -> None:
        """追加到 JSONL 日志."""
        if not self._initialized:
            return
        import datetime
        date_str = datetime.datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d")
        path = self._data_dir / f"usage_{date_str}.jsonl"
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("写入成本记录失败: %s", e)

    def get_session_summary(self) -> CostSummary:
        """获取当前会话的成本汇总."""
        return self._summarize(self._session_records)

    async def get_summary(self, days: int = 30) -> CostSummary:
        """获取指定天数内的成本汇总."""
        records = await self._load_records(days)
        return self._summarize(records)

    async def _load_records(self, days: int) -> list[UsageRecord]:
        """从文件加载记录."""
        import datetime
        records = []
        now = datetime.datetime.now()
        for i in range(days):
            date = now - datetime.timedelta(days=i)
            path = self._data_dir / f"usage_{date.strftime('%Y-%m-%d')}.jsonl"
            if not path.exists():
                continue
            try:
                for line in path.read_text(encoding="utf-8").strip().split("\n"):
                    if not line:
                        continue
                    d = json.loads(line)
                    records.append(UsageRecord(
                        timestamp=d.get("ts", 0),
                        provider=d.get("provider", ""),
                        model=d.get("model", ""),
                        input_tokens=d.get("in", 0),
                        output_tokens=d.get("out", 0),
                        total_tokens=d.get("in", 0) + d.get("out", 0),
                        total_cost=d.get("cost", 0),
                        route=d.get("route", ""),
                        success=d.get("ok", True),
                    ))
            except Exception as e:
                logger.warning("加载 %s 失败: %s", path, e)
        return records

    def _summarize(self, records: list[UsageRecord]) -> CostSummary:
        """汇总记录."""
        s = CostSummary()
        for r in records:
            s.total_cost += r.total_cost
            s.total_input_tokens += r.input_tokens
            s.total_output_tokens += r.output_tokens
            s.total_requests += 1
            if not r.success:
                s.failed_requests += 1
            s.by_provider[r.provider] = s.by_provider.get(r.provider, 0) + r.total_cost
            s.by_model[r.model] = s.by_model.get(r.model, 0) + r.total_cost
            if r.route:
                s.by_route[r.route] = s.by_route.get(r.route, 0) + r.total_cost
        return s

    async def export_csv(self, days: int = 30) -> str:
        """导出 CSV 格式报告."""
        records = await self._load_records(days)
        import datetime
        lines = ["timestamp,provider,model,input_tokens,output_tokens,cost,route,success"]
        for r in records:
            ts = datetime.datetime.fromtimestamp(r.timestamp).isoformat()
            lines.append(f"{ts},{r.provider},{r.model},{r.input_tokens},{r.output_tokens},{r.total_cost:.6f},{r.route},{r.success}")
        return "\n".join(lines)
