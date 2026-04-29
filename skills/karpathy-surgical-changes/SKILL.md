---
name: Surgical Changes
description: 只改必须改的，不顺手改无关代码。适用于修 bug、改功能、编辑现有文件。
version: 1.0.0
category: general
tags: [karpathy, coding-discipline, surgical, minimal-diff]
trigger: 修改 修复 改代码 fix bug 重构 编辑 surgical changes 改一下
tools: []
examples:
  - 修复这个 bug
  - 改一下这个函数
  - 更新这个接口
  - 重构这段代码
status: active
source: manual
author: karpathy-principles
---

# Surgical Changes

**核心原则：只动必须动的，清理只清理自己弄脏的。**

## 执行准则

### 1. 编辑现有代码时
- 不"顺手改进"旁边的代码、注释、格式
- 不重构没坏的东西
- 匹配现有代码风格，即使你会写得不一样
- 发现无关的死代码，提一嘴但不删

### 2. 你的改动产生的孤儿
- 你的改动导致某个 import/变量/函数不再使用 → 删掉
- 改动前就存在的死代码 → 不动，除非用户要求

### 3. Diff 自检
每一行改动都必须能追溯到用户的请求。如果不能，撤回那行改动。

### 4. 文件完整性
- 改文件前先读完整个文件
- 只改需要改的部分
- 不能丢失已有逻辑

## 反模式（禁止）

- 修一个 bug 顺手重命名了旁边的变量
- 改一个函数顺手给整个文件加了类型注解
- 做 A 功能时改了 B 功能的行为
- 没读完文件就开始改，导致丢失已有代码
