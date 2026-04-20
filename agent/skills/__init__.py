"""技能系统 — 自动学习 + 可复用技能 + 迭代优化.

组件:
- SkillManager — 技能管理 (创建/匹配/提取)
- LearningLoop — 四步学习闭环 (含失败驱动优化)
- SkillOptimizer — 失败分析 + 步骤精炼 (GEPA 理念)
- SkillEvaluator — 效果评估 + 自动废弃
- SkillComposer — 技能组合/流水线
- SkillCommunity — Markdown 导入/导出/分享
- ProceduralBridge — 程序记忆桥接 (技能↔记忆双向同步)
"""
