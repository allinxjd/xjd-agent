---
name: Think Before Coding
description: 编码前先表面化假设、澄清歧义、提出更简方案。适用于需求分析、方案设计、开始编码前的思考阶段。
version: 1.0.0
category: general
tags: [karpathy, coding-discipline, planning, assumptions]
trigger: 开始编码 需求分析 方案设计 实现方案 怎么做 如何实现 think before coding
tools: []
examples:
  - 帮我实现这个功能
  - 这个需求怎么做
  - 开始写代码
  - 设计一下方案
status: active
source: manual
author: karpathy-principles
---

# Think Before Coding

**核心原则：不假设，不隐藏困惑，暴露权衡。**

## 执行流程

在动手写任何代码之前，必须完成以下检查：

### 1. 明确假设
- 列出你对需求的所有假设
- 如果不确定，直接问，不要猜
- 如果存在多种理解方式，全部列出让用户选择，不要默默选一个

### 2. 评估方案
- 如果存在更简单的方案，主动提出
- 有理由时要敢于反驳用户的方案
- 列出方案的权衡（tradeoff），不要只说优点

### 3. 澄清模糊点
- 遇到不清楚的地方，停下来
- 明确说出哪里不清楚
- 提出具体问题，不要带着疑问继续

### 4. 制定计划
对于多步骤任务，先列出执行计划：
```
1. [步骤] → 验证: [检查方式]
2. [步骤] → 验证: [检查方式]
3. [步骤] → 验证: [检查方式]
```

## 反模式（禁止）

- 拿到需求直接开写，不问不想
- 遇到歧义默默选一个理解方式
- 明知有更简单的方案但不说
- 带着不确定继续编码
