---
name: Goal-Driven Execution
description: 定义可验证的成功标准，循环执行直到验证通过。适用于任务执行和验收阶段。
version: 1.0.0
category: general
tags: [karpathy, coding-discipline, verification, goal-driven, testing]
trigger: 验证 测试 验收 完成 goal driven 目标驱动 成功标准
tools: []
examples:
  - 这个功能做完了吗
  - 验证一下改动
  - 测试通过了吗
  - 怎么确认这个是对的
status: active
source: manual
author: karpathy-principles
---

# Goal-Driven Execution

**核心原则：定义成功标准，循环执行直到验证通过。**

## 执行准则

### 1. 把任务转化为可验证目标
- "加校验" → "写无效输入的测试，然后让测试通过"
- "修 bug" → "写复现 bug 的测试，然后让测试通过"
- "重构 X" → "确保重构前后测试都通过"

### 2. 多步骤任务必须有计划
```
1. [步骤] → 验证: [检查方式]
2. [步骤] → 验证: [检查方式]
3. [步骤] → 验证: [检查方式]
```

### 3. 验证循环
- 执行步骤
- 运行验证（测试/构建/手动检查）
- 失败 → 修复 → 重新验证
- 通过 → 下一步

### 4. 成功标准质量
- 强标准："所有测试通过 + 构建成功 + 手动验证核心路径" → 可以独立循环
- 弱标准："让它能用" → 需要不断澄清，效率低

## 反模式（禁止）

- 写完代码不跑测试就说完成了
- 改了代码不验证构建是否通过
- 没有成功标准就开始干活
- 测试失败了不修就跳过
