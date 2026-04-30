#!/bin/bash
# xjd-agent 强制更新脚本 — 绕过 updater，直接 git pull + pip install
# 用法: bash update.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

echo "=== xjd-agent force update ==="
echo "  目录: $REPO_DIR"

# 1. git pull
echo "  拉取最新代码..."
git pull --ff-only origin main

# 2. 检测 Python
PY=$(command -v python3.12 || command -v python3.11 || command -v python3)
if [ -z "$PY" ]; then
    echo "  [ERROR] 未找到 python3"
    exit 1
fi
echo "  Python: $PY ($($PY --version 2>&1))"

# 3. pip install --force-reinstall
echo "  安装中..."
IN_VENV=$($PY -c "import sys; print('yes' if sys.prefix != sys.base_prefix else 'no')")
if [ "$IN_VENV" = "yes" ]; then
    $PY -m pip install --force-reinstall . -q
else
    $PY -m pip install --force-reinstall . --break-system-packages -q 2>/dev/null || \
    $PY -m pip install --force-reinstall --user . --break-system-packages -q
fi

# 4. 显示版本
NEW_VER=$($PY -c "from importlib.metadata import version; print(version('xjd-agent'))" 2>/dev/null || echo "unknown")
echo "  更新完成! 版本: $NEW_VER"

# 5. 重启服务
if command -v xjd-agent &>/dev/null; then
    xjd-agent service restart 2>/dev/null && echo "  服务已重启" || echo "  请手动重启服务"
fi
