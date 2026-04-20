#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  XJD Agent 部署脚本
#
#  用法:
#    ./deploy.sh [install|start|stop|restart|status|update|logs]
# ═══════════════════════════════════════════════════════════════════

set -e

APP_NAME="xjd-agent"
APP_DIR="/opt/xjd-agent"
VENV_DIR="$APP_DIR/.venv"
DATA_DIR="$HOME/.xjd-agent"
PID_FILE="$DATA_DIR/gateway.pid"
LOG_FILE="$DATA_DIR/gateway.log"
PYTHON="${VENV_DIR}/bin/python"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[XJD]${NC} $1"; }
warn() { echo -e "${YELLOW}[XJD]${NC} $1"; }
err() { echo -e "${RED}[XJD]${NC} $1"; }

# ─── install ───
install() {
    log "安装 $APP_NAME..."

    # 检查 Python 3.11+
    if ! command -v python3 &>/dev/null; then
        err "需要 Python 3.11+。请先安装。"
        exit 1
    fi

    PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    log "Python 版本: $PY_VERSION"

    # 创建目录
    mkdir -p "$APP_DIR" "$DATA_DIR"

    # 创建 venv
    if [ ! -d "$VENV_DIR" ]; then
        log "创建虚拟环境..."
        python3 -m venv "$VENV_DIR"
    fi

    # 安装依赖
    log "安装依赖..."
    cd "$APP_DIR"
    "$VENV_DIR/bin/pip" install -U pip
    "$VENV_DIR/bin/pip" install -e ".[all]" 2>/dev/null || "$VENV_DIR/bin/pip" install -e "."

    log "安装完成！"
    log "运行 '$0 start' 启动服务"
}

# ─── start ───
start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        warn "服务已在运行 (PID: $(cat "$PID_FILE"))"
        return
    fi

    log "启动 Gateway..."
    mkdir -p "$DATA_DIR"

    cd "$APP_DIR"
    nohup "$VENV_DIR/bin/xjd-agent" gateway \
        --host 0.0.0.0 \
        --port 18789 \
        > "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    log "Gateway 已启动 (PID: $!)"
    log "日志: $LOG_FILE"
    log "WebSocket: ws://0.0.0.0:18789"
}

# ─── stop ───
stop() {
    if [ ! -f "$PID_FILE" ]; then
        warn "服务未运行"
        return
    fi

    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        log "停止服务 (PID: $PID)..."
        kill "$PID"
        sleep 2
        if kill -0 "$PID" 2>/dev/null; then
            warn "强制停止..."
            kill -9 "$PID"
        fi
    fi

    rm -f "$PID_FILE"
    log "服务已停止"
}

# ─── restart ───
restart() {
    stop
    sleep 1
    start
}

# ─── status ───
status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        log "服务运行中 (PID: $(cat "$PID_FILE"))"
    else
        warn "服务未运行"
    fi

    # 显示资源占用
    if command -v pgrep &>/dev/null; then
        PIDS=$(pgrep -f "xjd-agent" 2>/dev/null || true)
        if [ -n "$PIDS" ]; then
            ps -p "$PIDS" -o pid,pcpu,pmem,rss,cmd 2>/dev/null || true
        fi
    fi
}

# ─── update ───
update() {
    log "更新 $APP_NAME..."

    cd "$APP_DIR"
    git pull origin main

    "$VENV_DIR/bin/pip" install -e ".[all]" 2>/dev/null || "$VENV_DIR/bin/pip" install -e "."

    log "更新完成。运行 '$0 restart' 重启服务。"
}

# ─── logs ───
logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        warn "日志文件不存在: $LOG_FILE"
    fi
}

# ─── systemd ───
create_service() {
    log "创建 systemd service..."

    cat > /etc/systemd/system/xjd-agent.service << EOF
[Unit]
Description=XJD Agent - AI Agent Platform
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$APP_DIR
ExecStart=$VENV_DIR/bin/xjd-agent gateway --host 0.0.0.0 --port 18789
Restart=always
RestartSec=5
Environment=XJD_HOME=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable xjd-agent
    log "Service 已创建。使用 'systemctl start xjd-agent' 启动。"
}

# ─── main ───
case "${1:-}" in
    install)    install ;;
    start)      start ;;
    stop)       stop ;;
    restart)    restart ;;
    status)     status ;;
    update)     update ;;
    logs)       logs ;;
    service)    create_service ;;
    *)
        echo "用法: $0 {install|start|stop|restart|status|update|logs|service}"
        echo ""
        echo "命令:"
        echo "  install   安装 (创建 venv + 安装依赖)"
        echo "  start     启动 Gateway"
        echo "  stop      停止 Gateway"
        echo "  restart   重启"
        echo "  status    查看状态"
        echo "  update    更新 (git pull + pip install)"
        echo "  logs      查看日志"
        echo "  service   创建 systemd service"
        ;;
esac
