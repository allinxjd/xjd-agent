---
name: Simplicity First
description: 最少代码解决问题，不加未要求的功能，不做投机性抽象。适用于编码实现阶段。
version: 1.0.0
category: general
tags: [karpathy, coding-discipline, simplicity, minimal]
trigger: 写代码 编码 实现 开发 代码实现 simplicity first 简洁
tools: []
examples:
  - 写一个函数实现这个功能
  - 帮我开发这个模块
  - 实现这个接口
  - 写代码
status: active
source: manual
author: karpathy-principles
---

# Simplicity First

**核心原则：最少代码解决问题，不做任何投机性设计。**

## 执行准则

### 1. 只做被要求的
- 不加未要求的功能
- 不加未要求的配置项
- 不加未要求的灵活性
- 不为假想的未来需求设计

### 2. 不做无用抽象
- 只用一次的代码不需要抽象成函数
- 三行相似代码比一个过早抽象好
- 不写 helper/util 除非真的被多处调用

### 3. 不做无用防御
- 不为不可能的场景写错误处理
- 内部代码信任框架保证，只在系统边界做校验
- 不加 feature flag 或向后兼容层，直接改代码

### 4. 代码量自检
- 写完后问自己：200 行能不能 50 行搞定？
- 一个高级工程师会不会说这太复杂了？
- 如果是，重写

## 反模式（禁止）

- 写了一个只用一次的 BaseClass
- 加了 `config` 参数但只有一个值
- 为"万一以后需要"加了扩展点
- 对内部函数做参数校验
