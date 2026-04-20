"""记忆系统配置 — 集中管理所有可调阈值."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemoryConfig:
    """记忆系统配置.

    所有阈值集中在此，初始化时传入即可调整。
    """

    # --- 记忆管理 ---
    max_injection: int = 8           # 每次注入最多 N 条记忆
    extract_interval: int = 3        # 每 N 轮对话自动提取一次
    max_memories: int = 500          # 记忆总数上限

    # --- 去重 ---
    dedup_threshold: float = 0.75    # 组合分 > 此值 → 更新而非新建
    dedup_text_weight: float = 0.4   # 文本重叠权重
    dedup_semantic_weight: float = 0.6  # 语义搜索权重

    # --- 合并 ---
    consolidation_interval: int = 50   # 每 N 轮触发合并
    consolidation_threshold: float = 0.7  # 语义相似度阈值
    consolidation_hash_threshold: float = 0.8  # SimpleHashEmbedder 时的阈值
    consolidation_max_clusters: int = 10  # 每次最多处理 N 个聚类

    # --- 衰减 ---
    decay_interval: int = 20         # 每 N 轮触发衰减
    decay_low_days: int = 30         # low + 零访问 + N天 → 删除
    decay_medium_days: int = 90      # medium + 零访问 + N天 → 降级
    decay_weight_importance: float = 0.3
    decay_weight_recency: float = 0.2
    decay_weight_access: float = 0.2
    decay_weight_usefulness: float = 0.3

    # --- 反馈 ---
    feedback_ema_alpha: float = 0.8  # EMA 平滑系数
    usefulness_boost_min: float = 0.7  # 搜索排序: score *= (min + (1-min) * usefulness)
    usefulness_boost_range: float = 0.3

    # --- 反思 ---
    reflection_interval: int = 100   # 每 N 轮触发反思
    max_meta_memories: int = 20      # META 记忆上限
    max_reflections: int = 50        # reflections 表保留条数
    feedback_retention_days: int = 30  # feedback 表保留天数
    max_consolidation_history: int = 100  # 合并历史保留条数

    # --- 学习闭环 ---
    learning_interval: int = 5       # 每 N 轮触发学习
    min_tool_calls_for_skill: int = 2  # 至少 N 次工具调用才提取技能
