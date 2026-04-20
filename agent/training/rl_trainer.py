"""强化学习训练管线 — 基于对话反馈优化 Agent 行为.

- 奖励模型 (Reward Model) — 评估回答质量
- 策略优化 (PPO / DPO) — 根据奖励信号调整行为
- 经验回放 (Experience Replay) — 存储高质量/低质量对话样本
- 课程学习 (Curriculum Learning) — 从简单到复杂任务逐步训练
- A/B 实验 (Experiment) — 对比不同策略效果
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

class RewardSignal(str, Enum):
    """奖励信号类型."""

    EXPLICIT_POSITIVE = "explicit_positive"    # 用户明确表扬
    EXPLICIT_NEGATIVE = "explicit_negative"    # 用户明确批评
    IMPLICIT_CONTINUE = "implicit_continue"    # 用户继续对话 (正向)
    IMPLICIT_ABANDON = "implicit_abandon"      # 用户中断对话 (负向)
    TASK_SUCCESS = "task_success"              # 任务完成
    TASK_FAILURE = "task_failure"              # 任务失败
    TOOL_EFFICIENT = "tool_efficient"          # 工具调用效率高
    TOOL_WASTEFUL = "tool_wasteful"            # 工具调用浪费

@dataclass
class Experience:
    """经验样本 — 一次对话交互的完整记录."""

    experience_id: str = ""
    timestamp: float = 0.0

    # 输入
    user_message: str = ""
    context_messages: list[dict] = field(default_factory=list)
    system_prompt: str = ""

    # Agent 输出
    agent_response: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    thinking: str = ""

    # 奖励信号
    reward_signals: list[RewardSignal] = field(default_factory=list)
    reward_score: float = 0.0  # 综合奖励分 [-1, 1]

    # 元信息
    model_used: str = ""
    tokens_used: int = 0
    duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.experience_id,
            "ts": self.timestamp,
            "user_msg": self.user_message,
            "agent_resp": self.agent_response,
            "tool_calls": self.tool_calls,
            "reward_signals": [s.value for s in self.reward_signals],
            "reward_score": self.reward_score,
            "model": self.model_used,
            "tokens": self.tokens_used,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Experience:
        return cls(
            experience_id=d.get("id", ""),
            timestamp=d.get("ts", 0.0),
            user_message=d.get("user_msg", ""),
            agent_response=d.get("agent_resp", ""),
            tool_calls=d.get("tool_calls", []),
            reward_signals=[RewardSignal(s) for s in d.get("reward_signals", [])],
            reward_score=d.get("reward_score", 0.0),
            model_used=d.get("model", ""),
            tokens_used=d.get("tokens", 0),
            duration_ms=d.get("duration_ms", 0.0),
        )

@dataclass
class TrainingConfig:
    """训练配置."""

    # 经验回放
    replay_buffer_size: int = 10000
    min_experiences_for_training: int = 50
    batch_size: int = 32

    # 奖励
    reward_weights: dict[str, float] = field(default_factory=lambda: {
        "explicit_positive": 1.0,
        "explicit_negative": -1.0,
        "implicit_continue": 0.3,
        "implicit_abandon": -0.3,
        "task_success": 0.8,
        "task_failure": -0.5,
        "tool_efficient": 0.4,
        "tool_wasteful": -0.2,
    })

    # 策略优化
    learning_rate: float = 0.001
    discount_factor: float = 0.99
    entropy_coefficient: float = 0.01
    clip_range: float = 0.2  # PPO clip

    # 课程学习
    difficulty_levels: int = 5
    promotion_threshold: float = 0.7  # 晋级阈值
    demotion_threshold: float = 0.3   # 降级阈值

    # A/B 实验
    experiment_traffic_ratio: float = 0.1  # 10% 流量用于实验

class RewardModel:
    """奖励模型 — 评估 Agent 回答质量.

    综合多个信号计算奖励分：
    - 用户反馈 (显式)
    - 对话模式 (隐式)
    - 任务完成度
    - 工具使用效率
    - 回答质量指标 (长度、信息密度、相关性)
    """

    def __init__(self, config: TrainingConfig | None = None) -> None:
        self._config = config or TrainingConfig()
        self._quality_weights = {
            "length_penalty": -0.1,   # 太长扣分
            "brevity_penalty": -0.1,  # 太短扣分
            "tool_efficiency": 0.2,
            "response_time": 0.1,
        }

    def compute_reward(self, experience: Experience) -> float:
        """计算综合奖励分."""
        score = 0.0

        # 1. 基于显式/隐式信号
        for signal in experience.reward_signals:
            weight = self._config.reward_weights.get(signal.value, 0.0)
            score += weight

        # 2. 质量评估
        quality_score = self._evaluate_quality(experience)
        score += quality_score * 0.3

        # 3. 工具效率
        if experience.tool_calls:
            efficiency = self._evaluate_tool_efficiency(experience)
            score += efficiency * 0.2

        # 归一化到 [-1, 1]
        score = max(-1.0, min(1.0, score))
        return round(score, 4)

    def _evaluate_quality(self, exp: Experience) -> float:
        """评估回答质量."""
        score = 0.0
        response = exp.agent_response

        if not response:
            return -0.5

        # 长度适中
        length = len(response)
        if length < 10:
            score -= 0.3  # 太短
        elif length < 50:
            score -= 0.1
        elif length > 5000:
            score -= 0.2  # 太长
        else:
            score += 0.1

        # 信息密度 (去掉空白后的字符比例)
        non_space = len(response.replace(" ", "").replace("\n", ""))
        density = non_space / max(length, 1)
        if density > 0.5:
            score += 0.1

        # 响应时间
        if exp.duration_ms > 0:
            if exp.duration_ms < 2000:
                score += 0.1
            elif exp.duration_ms > 30000:
                score -= 0.2

        return score

    def _evaluate_tool_efficiency(self, exp: Experience) -> float:
        """评估工具使用效率."""
        n_calls = len(exp.tool_calls)

        if n_calls == 0:
            return 0.0

        # 检查是否有重复调用
        tool_names = [tc.get("name", "") for tc in exp.tool_calls]
        unique_tools = len(set(tool_names))
        repetition_ratio = unique_tools / n_calls

        score = 0.0
        if repetition_ratio > 0.8:
            score += 0.3  # 很少重复 → 高效
        elif repetition_ratio < 0.3:
            score -= 0.3  # 大量重复 → 低效

        # 调用次数
        if n_calls <= 3:
            score += 0.2
        elif n_calls > 15:
            score -= 0.3

        return score

class ExperienceReplayBuffer:
    """经验回放缓冲区 — 存储和采样经验.

    支持优先级采样 (Prioritized Experience Replay):
    - 高奖励/低奖励的经验有更高被采样概率
    - 新经验有更高优先级
    """

    def __init__(
        self,
        max_size: int = 10000,
        db_path: str | None = None,
    ) -> None:
        self._max_size = max_size
        self._buffer: list[Experience] = []
        self._priorities: list[float] = []
        self._db_path = db_path
        self._db = None

    async def initialize(self) -> None:
        """初始化 (加载持久化数据)."""
        if self._db_path:
            import aiosqlite
            self._db = await aiosqlite.connect(self._db_path)
            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS experiences (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    priority REAL DEFAULT 1.0,
                    created_at REAL DEFAULT 0
                )
            """)
            await self._db.commit()

            # 加载
            cursor = await self._db.execute(
                "SELECT data, priority FROM experiences ORDER BY created_at DESC LIMIT ?",
                (self._max_size,),
            )
            async for row in cursor:
                try:
                    exp = Experience.from_dict(json.loads(row[0]))
                    self._buffer.append(exp)
                    self._priorities.append(row[1])
                except Exception:
                    pass

            logger.info("ExperienceReplay loaded: %d experiences", len(self._buffer))

    async def add(self, experience: Experience) -> None:
        """添加经验."""
        # 计算优先级 (|reward| 越大越优先)
        priority = abs(experience.reward_score) + 0.1

        self._buffer.append(experience)
        self._priorities.append(priority)

        # 淘汰旧数据
        while len(self._buffer) > self._max_size:
            self._buffer.pop(0)
            self._priorities.pop(0)

        # 持久化
        if self._db:
            await self._db.execute(
                "INSERT OR REPLACE INTO experiences (id, data, priority, created_at) VALUES (?, ?, ?, ?)",
                (
                    experience.experience_id,
                    json.dumps(experience.to_dict(), ensure_ascii=False),
                    priority,
                    experience.timestamp,
                ),
            )
            await self._db.commit()

    def sample(self, batch_size: int = 32) -> list[Experience]:
        """优先级采样."""
        if not self._buffer:
            return []

        n = min(batch_size, len(self._buffer))

        # 概率分布 (softmax on priorities)
        total = sum(self._priorities)
        if total == 0:
            probs = [1.0 / len(self._priorities)] * len(self._priorities)
        else:
            probs = [p / total for p in self._priorities]

        indices = random.choices(range(len(self._buffer)), weights=probs, k=n)
        return [self._buffer[i] for i in indices]

    def get_positive_examples(self, n: int = 10) -> list[Experience]:
        """获取正面样本."""
        positives = [e for e in self._buffer if e.reward_score > 0.3]
        positives.sort(key=lambda e: e.reward_score, reverse=True)
        return positives[:n]

    def get_negative_examples(self, n: int = 10) -> list[Experience]:
        """获取负面样本."""
        negatives = [e for e in self._buffer if e.reward_score < -0.3]
        negatives.sort(key=lambda e: e.reward_score)
        return negatives[:n]

    @property
    def size(self) -> int:
        return len(self._buffer)

    def get_stats(self) -> dict[str, Any]:
        if not self._buffer:
            return {"size": 0}

        rewards = [e.reward_score for e in self._buffer]
        return {
            "size": len(self._buffer),
            "avg_reward": round(sum(rewards) / len(rewards), 4),
            "max_reward": round(max(rewards), 4),
            "min_reward": round(min(rewards), 4),
            "positive_ratio": round(
                len([r for r in rewards if r > 0]) / len(rewards), 3
            ),
        }

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

@dataclass
class PolicyUpdate:
    """策略更新记录."""

    update_id: str = ""
    timestamp: float = 0.0
    update_type: str = ""  # "prompt_tune", "tool_preference", "style_adjust"

    # 更新内容
    changes: dict[str, Any] = field(default_factory=dict)

    # 效果指标
    pre_reward: float = 0.0
    post_reward: float = 0.0
    improvement: float = 0.0

class PolicyOptimizer:
    """策略优化器 — 基于经验调整 Agent 行为.

    优化维度:
    1. System Prompt 微调 (最有效)
    2. 工具选择偏好
    3. 回复风格/长度
    4. 错误恢复策略
    """

    def __init__(self, config: TrainingConfig | None = None) -> None:
        self._config = config or TrainingConfig()
        self._updates: list[PolicyUpdate] = []

        # 当前策略参数
        self._policy: dict[str, Any] = {
            "preferred_response_length": "medium",  # short/medium/long
            "tool_preference_scores": {},            # tool_name → preference
            "style_hints": [],                       # 风格提示
            "error_recovery": "retry_once",          # retry_once/escalate/fallback
            "verbosity": 0.5,                        # 0-1
        }

    @property
    def policy(self) -> dict[str, Any]:
        return self._policy.copy()

    def optimize_from_batch(self, experiences: list[Experience]) -> Optional[PolicyUpdate]:
        """从一批经验中优化策略.

        使用简化版 DPO (Direct Preference Optimization):
        对比正面和负面经验，提取模式差异。
        """
        if not experiences:
            return None

        positives = [e for e in experiences if e.reward_score > 0.2]
        negatives = [e for e in experiences if e.reward_score < -0.2]

        if not positives or not negatives:
            return None

        changes = {}

        # 1. 回复长度偏好
        avg_pos_len = sum(len(e.agent_response) for e in positives) / len(positives)
        avg_neg_len = sum(len(e.agent_response) for e in negatives) / len(negatives)

        if avg_pos_len < avg_neg_len * 0.7:
            changes["preferred_response_length"] = "short"
            self._policy["preferred_response_length"] = "short"
        elif avg_pos_len > avg_neg_len * 1.5:
            changes["preferred_response_length"] = "long"
            self._policy["preferred_response_length"] = "long"

        # 2. 工具偏好
        pos_tools: dict[str, int] = {}
        neg_tools: dict[str, int] = {}
        for e in positives:
            for tc in e.tool_calls:
                name = tc.get("name", "")
                pos_tools[name] = pos_tools.get(name, 0) + 1
        for e in negatives:
            for tc in e.tool_calls:
                name = tc.get("name", "")
                neg_tools[name] = neg_tools.get(name, 0) + 1

        all_tools = set(pos_tools.keys()) | set(neg_tools.keys())
        for tool in all_tools:
            pos_count = pos_tools.get(tool, 0)
            neg_count = neg_tools.get(tool, 0)
            total = pos_count + neg_count
            if total > 0:
                preference = (pos_count - neg_count) / total
                self._policy["tool_preference_scores"][tool] = round(preference, 3)
                changes[f"tool:{tool}"] = round(preference, 3)

        # 3. 详细程度
        pos_detail = sum(len(e.agent_response.split("\n")) for e in positives) / len(positives)
        neg_detail = sum(len(e.agent_response.split("\n")) for e in negatives) / len(negatives)
        if pos_detail > neg_detail * 1.3:
            self._policy["verbosity"] = min(1.0, self._policy["verbosity"] + 0.1)
            changes["verbosity"] = self._policy["verbosity"]
        elif pos_detail < neg_detail * 0.7:
            self._policy["verbosity"] = max(0.0, self._policy["verbosity"] - 0.1)
            changes["verbosity"] = self._policy["verbosity"]

        if not changes:
            return None

        update = PolicyUpdate(
            update_id=f"update_{int(time.time())}",
            timestamp=time.time(),
            update_type="batch_optimize",
            changes=changes,
            pre_reward=round(sum(e.reward_score for e in experiences) / len(experiences), 4),
        )
        self._updates.append(update)
        return update

    def generate_system_prompt_hints(self) -> str:
        """根据当前策略生成 system prompt 补充指令."""
        hints = []

        length = self._policy.get("preferred_response_length", "medium")
        if length == "short":
            hints.append("请尽量简洁回答，避免冗余。")
        elif length == "long":
            hints.append("请提供详细完整的回答。")

        verbosity = self._policy.get("verbosity", 0.5)
        if verbosity > 0.7:
            hints.append("可以包含解释、示例和补充信息。")
        elif verbosity < 0.3:
            hints.append("直接给出答案，减少解释。")

        # 工具偏好提示
        prefs = self._policy.get("tool_preference_scores", {})
        preferred = [t for t, s in prefs.items() if s > 0.5]
        avoided = [t for t, s in prefs.items() if s < -0.5]
        if preferred:
            hints.append(f"优先使用工具: {', '.join(preferred)}")
        if avoided:
            hints.append(f"谨慎使用工具: {', '.join(avoided)}")

        for style in self._policy.get("style_hints", []):
            hints.append(style)

        return "\n".join(hints)

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_updates": len(self._updates),
            "current_policy": self._policy,
            "last_update": self._updates[-1].to_dict() if self._updates else None,
        }

class CurriculumScheduler:
    """课程学习调度器 — 渐进式提升任务难度.

    难度等级:
    1. 简单问答 (事实查询、翻译)
    2. 分析任务 (摘要、对比)
    3. 工具使用 (单工具)
    4. 复杂推理 (多步、多工具)
    5. 创造性任务 (写作、设计方案)
    """

    def __init__(self, config: TrainingConfig | None = None) -> None:
        self._config = config or TrainingConfig()
        self._current_level = 1
        self._level_scores: dict[int, list[float]] = {i: [] for i in range(1, 6)}
        self._task_categories = {
            1: ["qa", "translation", "lookup"],
            2: ["summary", "comparison", "analysis"],
            3: ["single_tool", "web_search", "file_operation"],
            4: ["multi_step", "multi_tool", "reasoning"],
            5: ["creative_writing", "design", "planning"],
        }

    @property
    def current_level(self) -> int:
        return self._current_level

    def classify_difficulty(self, user_message: str, tool_calls: list[dict]) -> int:
        """估算任务难度."""
        n_tools = len(tool_calls)
        msg_len = len(user_message)

        # 简单启发式
        if n_tools == 0 and msg_len < 50:
            return 1
        elif n_tools == 0:
            return 2
        elif n_tools <= 2:
            return 3
        elif n_tools <= 5:
            return 4
        else:
            return 5

    def record_performance(self, level: int, reward: float) -> None:
        """记录在某难度等级的表现."""
        scores = self._level_scores.get(level, [])
        scores.append(reward)
        # 只保留最近 100 个
        if len(scores) > 100:
            scores = scores[-100:]
        self._level_scores[level] = scores

        self._maybe_adjust_level()

    def _maybe_adjust_level(self) -> None:
        """根据表现调整当前难度."""
        scores = self._level_scores.get(self._current_level, [])
        if len(scores) < 10:
            return

        recent = scores[-20:]
        avg = sum(recent) / len(recent)

        if avg >= self._config.promotion_threshold and self._current_level < 5:
            self._current_level += 1
            logger.info("Curriculum: promoted to level %d (avg=%.2f)", self._current_level, avg)
        elif avg <= self._config.demotion_threshold and self._current_level > 1:
            self._current_level -= 1
            logger.info("Curriculum: demoted to level %d (avg=%.2f)", self._current_level, avg)

    def get_stats(self) -> dict[str, Any]:
        stats = {"current_level": self._current_level, "levels": {}}
        for level, scores in self._level_scores.items():
            if scores:
                stats["levels"][level] = {
                    "count": len(scores),
                    "avg": round(sum(scores) / len(scores), 3),
                }
            else:
                stats["levels"][level] = {"count": 0, "avg": 0.0}
        return stats

@dataclass
class Experiment:
    """A/B 实验."""

    experiment_id: str = ""
    name: str = ""
    description: str = ""
    started_at: float = 0.0
    ended_at: float = 0.0
    active: bool = True

    # 变体
    control_policy: dict[str, Any] = field(default_factory=dict)
    treatment_policy: dict[str, Any] = field(default_factory=dict)

    # 结果
    control_rewards: list[float] = field(default_factory=list)
    treatment_rewards: list[float] = field(default_factory=list)

    def get_results(self) -> dict[str, Any]:
        ctrl_n = len(self.control_rewards)
        treat_n = len(self.treatment_rewards)

        ctrl_avg = sum(self.control_rewards) / ctrl_n if ctrl_n else 0
        treat_avg = sum(self.treatment_rewards) / treat_n if treat_n else 0

        return {
            "experiment": self.name,
            "control": {"n": ctrl_n, "avg_reward": round(ctrl_avg, 4)},
            "treatment": {"n": treat_n, "avg_reward": round(treat_avg, 4)},
            "improvement": round(treat_avg - ctrl_avg, 4) if ctrl_n and treat_n else None,
            "significant": self._is_significant() if ctrl_n > 20 and treat_n > 20 else None,
        }

    def _is_significant(self) -> bool:
        """简单 Z-test 显著性检验."""
        if not self.control_rewards or not self.treatment_rewards:
            return False

        n1 = len(self.control_rewards)
        n2 = len(self.treatment_rewards)
        mean1 = sum(self.control_rewards) / n1
        mean2 = sum(self.treatment_rewards) / n2
        var1 = sum((x - mean1) ** 2 for x in self.control_rewards) / n1
        var2 = sum((x - mean2) ** 2 for x in self.treatment_rewards) / n2

        se = math.sqrt(var1 / n1 + var2 / n2) if (var1 / n1 + var2 / n2) > 0 else 1
        z = abs(mean2 - mean1) / se

        return z > 1.96  # 95% 置信度

class RLTrainer:
    """强化学习训练器 — 集成所有 RL 组件.

    用法:
        trainer = RLTrainer()
        await trainer.initialize()

        # 记录经验
        await trainer.record_experience(experience)

        # 触发训练
        if trainer.should_train():
            update = await trainer.train_step()

        # 获取策略建议
        hints = trainer.get_policy_hints()
    """

    def __init__(
        self,
        config: TrainingConfig | None = None,
        db_path: str | None = None,
    ) -> None:
        self._config = config or TrainingConfig()

        if not db_path:
            from agent.core.config import get_home
            db_path = str(get_home() / "rl_training.db")

        self._reward_model = RewardModel(self._config)
        self._replay_buffer = ExperienceReplayBuffer(
            max_size=self._config.replay_buffer_size,
            db_path=db_path,
        )
        self._optimizer = PolicyOptimizer(self._config)
        self._curriculum = CurriculumScheduler(self._config)

        # 实验
        self._experiments: dict[str, Experiment] = {}
        self._active_experiment: Optional[str] = None

        # 统计
        self._total_experiences = 0
        self._total_updates = 0
        self._last_train_time = 0.0

    async def initialize(self) -> None:
        """初始化训练系统."""
        await self._replay_buffer.initialize()
        logger.info(
            "RLTrainer initialized: buffer=%d experiences",
            self._replay_buffer.size,
        )

    async def record_experience(self, experience: Experience) -> float:
        """记录一次经验并计算奖励.

        Returns:
            计算的奖励分数
        """
        # 计算奖励
        reward = self._reward_model.compute_reward(experience)
        experience.reward_score = reward

        # 课程学习记录
        difficulty = self._curriculum.classify_difficulty(
            experience.user_message,
            experience.tool_calls,
        )
        self._curriculum.record_performance(difficulty, reward)

        # A/B 实验
        if self._active_experiment and self._active_experiment in self._experiments:
            exp = self._experiments[self._active_experiment]
            if exp.active:
                # 随机分配
                if random.random() < self._config.experiment_traffic_ratio:
                    exp.treatment_rewards.append(reward)
                else:
                    exp.control_rewards.append(reward)

        # 存入回放缓冲区
        await self._replay_buffer.add(experience)
        self._total_experiences += 1

        return reward

    def should_train(self) -> bool:
        """是否应该触发训练."""
        if self._replay_buffer.size < self._config.min_experiences_for_training:
            return False

        # 至少间隔 5 分钟
        if time.time() - self._last_train_time < 300:
            return False

        return True

    async def train_step(self) -> Optional[PolicyUpdate]:
        """执行一步训练."""
        batch = self._replay_buffer.sample(self._config.batch_size)
        if not batch:
            return None

        update = self._optimizer.optimize_from_batch(batch)
        if update:
            self._total_updates += 1
            self._last_train_time = time.time()
            logger.info(
                "Training step %d: %s",
                self._total_updates,
                json.dumps(update.changes, ensure_ascii=False),
            )

        return update

    def get_policy_hints(self) -> str:
        """获取当前策略的 system prompt 补充."""
        return self._optimizer.generate_system_prompt_hints()

    def start_experiment(
        self,
        name: str,
        treatment_changes: dict[str, Any],
        description: str = "",
    ) -> str:
        """启动 A/B 实验."""
        exp_id = f"exp_{int(time.time())}"
        experiment = Experiment(
            experiment_id=exp_id,
            name=name,
            description=description,
            started_at=time.time(),
            control_policy=self._optimizer.policy,
            treatment_policy={**self._optimizer.policy, **treatment_changes},
        )
        self._experiments[exp_id] = experiment
        self._active_experiment = exp_id
        logger.info("A/B experiment started: %s", name)
        return exp_id

    def end_experiment(self, exp_id: str) -> Optional[dict[str, Any]]:
        """结束实验并返回结果."""
        exp = self._experiments.get(exp_id)
        if not exp:
            return None

        exp.active = False
        exp.ended_at = time.time()
        if self._active_experiment == exp_id:
            self._active_experiment = None

        return exp.get_results()

    def get_stats(self) -> dict[str, Any]:
        return {
            "total_experiences": self._total_experiences,
            "total_updates": self._total_updates,
            "buffer": self._replay_buffer.get_stats(),
            "curriculum": self._curriculum.get_stats(),
            "policy": self._optimizer.policy,
            "active_experiment": self._active_experiment,
        }

    async def close(self) -> None:
        await self._replay_buffer.close()
