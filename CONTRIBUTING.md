# Contributing / 贡献指南

[English](#english) | [中文](#中文)

---

## English

### Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/<you>/xjd-agent.git`
3. Create a branch: `git checkout -b feature/my-feature`
4. Install dev dependencies: `make dev`

### Development Workflow

```bash
# Run tests
make test

# Lint
make lint

# Type check
make typecheck

# Format code
make format
```

### Code Style

- Python 3.11+, formatted with [Ruff](https://docs.astral.sh/ruff/)
- Line length: 100 characters
- Type hints required for public APIs
- Docstrings in Chinese or English

### Pull Request Process

1. Ensure all tests pass: `make test`
2. Ensure lint passes: `make lint`
3. Update documentation if needed
4. Write a clear PR description
5. One feature per PR

### Reporting Issues

- Use GitHub Issues
- Include: Python version, OS, steps to reproduce, expected vs actual behavior

---

## 中文

### 开始贡献

1. Fork 本仓库
2. 克隆: `git clone https://github.com/<你的用户名>/xjd-agent.git`
3. 创建分支: `git checkout -b feature/我的功能`
4. 安装开发依赖: `make dev`

### 开发流程

```bash
# 运行测试
make test

# 代码检查
make lint

# 类型检查
make typecheck

# 格式化
make format
```

### 代码规范

- Python 3.11+，使用 [Ruff](https://docs.astral.sh/ruff/) 格式化
- 行宽: 100 字符
- 公开 API 需要类型注解
- 文档字符串中英文均可

### PR 流程

1. 确保测试通过: `make test`
2. 确保 lint 通过: `make lint`
3. 如有需要，更新文档
4. 写清楚 PR 描述
5. 一个 PR 只做一件事

### 报告问题

- 使用 GitHub Issues
- 包含: Python 版本、操作系统、复现步骤、期望行为 vs 实际行为
