# CLAUDE.md — xjd-agent 开发规范

## 项目概览

xjd-agent 是一个 Python AI Agent 平台，支持 14+ 模型供应商、20+ 消息适配器、40+ 工具、技能系统、AI Company 多角色协作。

## 构建与测试

```bash
# 运行测试（必须全过才能提交）
python3 -m pytest tests/ -x -q

# 隔离环境验证安装（涉及 updater/pip/package-data 改动时必须执行）
python3.12 -m venv /tmp/test-venv
/tmp/test-venv/bin/pip install .
/tmp/test-venv/bin/python3.12 -c "from agent.skills.manager import SkillManager; print('OK')"
rm -rf /tmp/test-venv

# 启动 WebUI 验证前端改动
xjd-agent gateway
```

## Must Always

- 改动前先读完相关文件，理解现有逻辑再动手
- 每次改动后立即运行 `pytest tests/ -x -q`，全过才能继续
- 涉及安装/更新逻辑（updater、pip install、package-data）的改动，必须在隔离 venv 中完整验证
- 涉及前端改动，必须启动 WebUI 实际验证，不能只靠代码审查
- 提交前审计所有改动文件的完整 diff，逐行确认无遗漏
- 一次性考虑完整链路：新功能 → 已有用户升级 → 全新安装 → 边界情况
- 改动只改需要改的部分，保留文件中所有已有逻辑
- push 前用 `git diff HEAD` 做最终审计

## Must Never

- 改 A 功能时顺手改 B 的行为逻辑（任何行为变更必须先问用户）
- 提交未经测试的代码
- 用 `--no-deps` 参数运行 pip install（会删除依赖）
- 全量覆盖配置文件（config.save() 必须 merge 现有内容）
- 在代码或注释中出现竞品平台名称
- 自作主张加 emoji 图标
- 连接服务器（SSH/SCP）除非用户明确要求
- 改一个点就推 — 必须端到端验证完整流程
- 表面审计：不能只看代码"应该没问题"，必须实际运行验证

## 开发流程

### 修 Bug

1. 复现问题，理解根因
2. 写修复代码
3. 运行 pytest 确认无回归
4. 如涉及安装流程，在隔离 venv 中验证
5. `git diff HEAD` 审计所有改动
6. 确认无问题后提交

### 新功能

1. 读懂现有代码结构和模式
2. 设计方案，考虑：已有用户升级路径、全新安装、向后兼容
3. 实现代码
4. 运行 pytest
5. 如涉及 UI，启动 WebUI 实际验证
6. 如涉及安装，隔离 venv 验证
7. `git diff HEAD` 完整审计
8. 提交

### 安装/更新相关改动（高风险）

必须在隔离环境中跑完整流程：
1. 创建干净 venv
2. `pip install .` 模拟用户安装
3. 从非源码目录运行，验证功能正常
4. 模拟 `update --auto` 全流程
5. 确认依赖完整、CLI 能启动、技能能加载

## 代码规范

- Python 3.11+，使用 type annotations
- 遵循 PEP 8
- 使用 dataclass 做数据结构
- 新字段必须有默认值（向后兼容）
- 异常处理要具体，不要裸 `except Exception`
- 日志用 `logger.warning/info/debug`，不用 print

## 项目结构

```
agent/           # 核心代码
  core/          # 配置、更新器、密钥管理
  skills/        # 技能管理器
  tools/         # 工具注册
  company/       # AI Company 多角色协作
  builtin_skills/  # 随包分发的内置技能
web/             # WebUI（aiohttp + 静态文件）
  server.py      # API 路由
  static/        # 前端 HTML/JS/CSS
skills/          # 源码中的技能定义（开发用）
cli/             # CLI 命令
tests/           # pytest 测试
```

## 关键约束

- `builtin_skills/` 必须与 `skills/` 保持同步
- SKILL.md 的 secrets 声明中 group 字段用于前端分组显示
- updater 优先级：git pull → tarball → pip（xjd-agent 不在 PyPI 上）
- `--force-reinstall` 不能带 `--no-deps`
- builtin 技能版本更新时自动同步用户目录的 SKILL.md
