#!/bin/bash

# 飞书机器人部署脚本
# 作者：小巨蛋智能体
# 版本：1.0.0

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查依赖
check_dependencies() {
    log_info "检查系统依赖..."
    
    # 检查 Python
    if ! command -v python3 &> /dev/null; then
        log_error "Python3 未安装，请先安装 Python3"
        exit 1
    fi
    
    # 检查 pip
    if ! command -v pip3 &> /dev/null; then
        log_error "pip3 未安装，请先安装 pip3"
        exit 1
    fi
    
    log_success "系统依赖检查通过"
}

# 安装 Python 依赖
install_dependencies() {
    log_info "安装 Python 依赖..."
    
    # 创建虚拟环境（如果不存在）
    if [ ! -d ".venv" ]; then
        log_info "创建 Python 虚拟环境..."
        python3 -m venv .venv
    fi
    
    # 激活虚拟环境
    source .venv/bin/activate
    
    # 安装基础依赖
    pip3 install --upgrade pip
    
    # 安装飞书适配器
    log_info "安装飞书适配器..."
    pip3 install "xjd-agent[feishu]"
    
    # 安装其他依赖
    pip3 install pyyaml requests
    
    log_success "Python 依赖安装完成"
}

# 配置飞书机器人
configure_feishu() {
    log_info "配置飞书机器人..."
    
    # 检查配置文件是否存在
    if [ ! -f "config/feishu_config.yaml" ]; then
        log_error "配置文件不存在: config/feishu_config.yaml"
        log_info "请先填写配置文件中的飞书开放平台信息"
        exit 1
    fi
    
    # 显示配置说明
    echo ""
    log_warning "请确保已完成以下飞书开放平台配置："
    echo "1. 访问 https://open.feishu.cn/app"
    echo "2. 创建企业自建应用"
    echo "3. 开启机器人能力"
    echo "4. 获取以下信息："
    echo "   - App ID"
    echo "   - App Secret"
    echo "   - Verification Token"
    echo "   - Encrypt Key（可选）"
    echo ""
    
    read -p "是否已获取上述信息？(y/n): " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "请先获取飞书开放平台信息，然后重新运行脚本"
        exit 0
    fi
    
    # 编辑配置文件
    log_info "请编辑配置文件 config/feishu_config.yaml"
    log_info "将 YOUR_APP_ID 等占位符替换为实际值"
    echo ""
    
    # 询问是否现在编辑
    read -p "是否现在编辑配置文件？(y/n): " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if command -v nano &> /dev/null; then
            nano config/feishu_config.yaml
        elif command -v vim &> /dev/null; then
            vim config/feishu_config.yaml
        else
            vi config/feishu_config.yaml
        fi
    fi
    
    log_success "飞书配置完成"
}

# 配置 Webhook（如果需要）
configure_webhook() {
    log_info "配置 Webhook..."
    
    # 读取配置文件中的模式
    MODE=$(grep -E "^\s*mode:" config/feishu_config.yaml | awk '{print $2}' | tr -d '"')
    
    if [ "$MODE" = "webhook" ]; then
        log_warning "Webhook 模式需要公网可访问的地址"
        echo ""
        log_info "请确保："
        echo "1. 服务器有公网 IP 或域名"
        echo "2. 防火墙开放了 9001 端口"
        echo "3. 配置了反向代理（可选）"
        echo ""
        
        # 获取公网 IP（尝试）
        log_info "尝试获取公网 IP..."
        PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "未知")
        
        if [ "$PUBLIC_IP" != "未知" ]; then
            log_info "检测到的公网 IP: $PUBLIC_IP"
            log_info "Webhook URL 应为: http://$PUBLIC_IP:9001/feishu/webhook"
        else
            log_warning "无法自动获取公网 IP，请手动确定"
        fi
        
        echo ""
        log_info "需要在飞书开放平台配置事件订阅："
        echo "1. 进入应用后台 → 事件订阅"
        echo "2. 请求地址填写: http://你的域名或IP:9001/feishu/webhook"
        echo "3. 添加事件: im.message.receive_v1"
        echo "4. 添加权限: im:message, im:message:send_as_bot"
        echo ""
        
        read -p "是否已配置飞书事件订阅？(y/n): " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_warning "请先配置飞书事件订阅，否则机器人无法接收消息"
        fi
        
    elif [ "$MODE" = "long_poll" ]; then
        log_success "使用长连接模式，无需公网 IP"
    else
        log_error "未知模式: $MODE，请检查配置文件"
        exit 1
    fi
    
    log_success "Webhook 配置完成"
}

# 启动机器人
start_bot() {
    log_info "启动飞书机器人..."
    
    # 激活虚拟环境
    source .venv/bin/activate
    
    # 创建日志目录
    mkdir -p logs
    
    # 启动网关
    log_info "启动小巨蛋网关..."
    echo ""
    log_warning "按 Ctrl+C 停止机器人"
    echo ""
    
    # 启动命令
    xjd-agent gateway --config config/feishu_config.yaml
    
    if [ $? -ne 0 ]; then
        log_error "启动失败，请检查配置和日志"
        exit 1
    fi
}

# 测试机器人
test_bot() {
    log_info "测试机器人功能..."
    
    # 激活虚拟环境
    source .venv/bin/activate
    
    # 运行测试脚本
    if [ -f "skills/feishu_group_management.py" ]; then
        log_info "运行群管理测试..."
        python3 skills/feishu_group_management.py
    fi
    
    log_success "测试完成"
}

# 显示帮助
show_help() {
    echo "飞书机器人部署脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  install     安装依赖和配置"
    echo "  configure   仅配置飞书"
    echo "  start       启动机器人"
    echo "  test        测试功能"
    echo "  all         完整部署（默认）"
    echo "  help        显示此帮助"
    echo ""
    echo "示例:"
    echo "  $0 install    # 安装依赖"
    echo "  $0 start      # 启动机器人"
    echo "  $0 all        # 完整部署"
}

# 主函数
main() {
    ACTION=${1:-"all"}
    
    case $ACTION in
        "install")
            check_dependencies
            install_dependencies
            ;;
        "configure")
            configure_feishu
            configure_webhook
            ;;
        "start")
            start_bot
            ;;
        "test")
            test_bot
            ;;
        "all")
            check_dependencies
            install_dependencies
            configure_feishu
            configure_webhook
            test_bot
            log_success "部署完成！"
            log_info "运行 '$0 start' 启动机器人"
            ;;
        "help")
            show_help
            ;;
        *)
            log_error "未知选项: $ACTION"
            show_help
            exit 1
            ;;
    esac
}

# 运行主函数
main "$@"