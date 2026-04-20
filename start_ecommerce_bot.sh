#!/bin/bash

# 电商作图飞书机器人快速启动脚本
# 作者：小巨蛋智能体

set -e

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}  电商作图飞书机器人启动脚本${NC}"
echo -e "${BLUE}========================================${NC}"

# 检查Python环境
check_python() {
    echo -e "${BLUE}[1/6] 检查Python环境...${NC}"
    if ! command -v python3 &> /dev/null; then
        echo -e "${RED}错误: Python3 未安装${NC}"
        exit 1
    fi
    
    python_version=$(python3 --version | cut -d' ' -f2)
    echo -e "${GREEN}✓ Python版本: ${python_version}${NC}"
}

# 检查虚拟环境
check_venv() {
    echo -e "${BLUE}[2/6] 检查虚拟环境...${NC}"
    if [ ! -d ".venv" ]; then
        echo -e "${YELLOW}虚拟环境不存在，正在创建...${NC}"
        python3 -m venv .venv
        echo -e "${GREEN}✓ 虚拟环境创建成功${NC}"
    else
        echo -e "${GREEN}✓ 虚拟环境已存在${NC}"
    fi
}

# 激活虚拟环境并安装依赖
install_dependencies() {
    echo -e "${BLUE}[3/6] 安装依赖...${NC}"
    source .venv/bin/activate
    
    # 检查是否已安装xjd-agent
    if ! python3 -c "import xjd_agent" &> /dev/null; then
        echo -e "${YELLOW}正在安装xjd-agent...${NC}"
        pip install -e .
        echo -e "${GREEN}✓ xjd-agent安装成功${NC}"
    else
        echo -e "${GREEN}✓ xjd-agent已安装${NC}"
    fi
    
    # 安装飞书相关依赖
    echo -e "${YELLOW}安装飞书依赖...${NC}"
    pip install "xjd-agent[feishu]" pyyaml requests
    echo -e "${GREEN}✓ 飞书依赖安装成功${NC}"
}

# 检查配置文件
check_config() {
    echo -e "${BLUE}[4/6] 检查配置文件...${NC}"
    
    # 检查环境配置文件
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            echo -e "${YELLOW}创建.env配置文件...${NC}"
            cp .env.example .env
            echo -e "${GREEN}✓ .env文件已创建，请编辑配置飞书信息${NC}"
        else
            echo -e "${RED}错误: .env.example文件不存在${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}✓ .env文件已存在${NC}"
    fi
    
    # 检查飞书配置文件
    if [ ! -f "config/ecommerce_feishu_config.yaml" ]; then
        echo -e "${RED}错误: 电商飞书配置文件不存在${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✓ 配置文件检查完成${NC}"
}

# 显示配置指南
show_config_guide() {
    echo -e "${BLUE}[5/6] 飞书配置指南${NC}"
    echo -e "${YELLOW}========================================${NC}"
    echo -e "请完成以下配置："
    echo -e ""
    echo -e "1. 访问 ${GREEN}https://open.feishu.cn/app${NC}"
    echo -e "2. 创建企业自建应用"
    echo -e "3. 开启机器人能力"
    echo -e "4. 获取以下信息："
    echo -e "   • ${YELLOW}App ID${NC}"
    echo -e "   • ${YELLOW}App Secret${NC}"
    echo -e "   • ${YELLOW}Verification Token${NC}"
    echo -e ""
    echo -e "5. 编辑配置文件："
    echo -e "   ${YELLOW}nano config/ecommerce_feishu_config.yaml${NC}"
    echo -e ""
    echo -e "6. 配置事件订阅："
    echo -e "   • 请求地址：${GREEN}http://你的域名或IP:9001/feishu/webhook${NC}"
    echo -e "   • 添加事件：im.message.receive_v1"
    echo -e "   • 添加权限：im:message, im:message:send_as_bot"
    echo -e "${YELLOW}========================================${NC}"
    
    read -p "是否已配置完成？(y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}请先完成飞书配置，然后重新运行此脚本${NC}"
        exit 0
    fi
}

# 启动机器人
start_bot() {
    echo -e "${BLUE}[6/6] 启动电商作图机器人...${NC}"
    
    # 激活虚拟环境
    source .venv/bin/activate
    
    # 创建日志目录
    mkdir -p logs
    
    echo -e "${GREEN}启动命令：${NC}"
    echo -e "${YELLOW}xjd-agent gateway --config config/ecommerce_feishu_config.yaml${NC}"
    echo -e ""
    echo -e "${BLUE}机器人正在启动...${NC}"
    echo -e "${YELLOW}按 Ctrl+C 停止机器人${NC}"
    echo -e ""
    
    # 启动机器人
    xjd-agent gateway --config config/ecommerce_feishu_config.yaml
}

# 显示功能列表
show_features() {
    echo -e "${GREEN}========================================${NC}"
    echo -e "${BLUE}  电商作图机器人功能列表${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo -e ""
    echo -e "${YELLOW}📸 图片生成功能：${NC}"
    echo -e "  • 白底图生成 - 符合淘宝/天猫规范"
    echo -e "  • 商品详情图 - 多图组合展示"
    echo -e "  • 透明底图 - 抖音平台专用"
    echo -e "  • 营销图片 - 促销活动图片"
    echo -e ""
    echo -e "${YELLOW}📋 规范查询：${NC}"
    echo -e "  • 平台图片规范"
    echo -e "  • 尺寸要求指南"
    echo -e "  • 格式建议"
    echo -e ""
    echo -e "${YELLOW}🔄 批量处理：${NC}"
    echo -e "  • 批量白底图生成"
    echo -e "  • 批量详情图制作"
    echo -e "  • 批量格式转换"
    echo -e ""
    echo -e "${YELLOW}💡 使用示例：${NC}"
    echo -e "  在飞书群中发送："
    echo -e "  • \"生成淘宝白底图\""
    echo -e "  • \"制作手机详情图\""
    echo -e "  • \"抖音透明底图要求\""
    echo -e "  • \"批量处理服装图片\""
    echo -e ""
    echo -e "${GREEN}========================================${NC}"
}

# 主函数
main() {
    case "$1" in
        "features")
            show_features
            ;;
        "config")
            check_config
            show_config_guide
            ;;
        "start")
            check_python
            check_venv
            install_dependencies
            check_config
            start_bot
            ;;
        "install")
            check_python
            check_venv
            install_dependencies
            check_config
            show_config_guide
            ;;
        *)
            echo -e "${BLUE}使用方法：${NC}"
            echo -e "  ${GREEN}./start_ecommerce_bot.sh features${NC}   - 显示功能列表"
            echo -e "  ${GREEN}./start_ecommerce_bot.sh config${NC}    - 显示配置指南"
            echo -e "  ${GREEN}./start_ecommerce_bot.sh install${NC}   - 安装依赖和配置"
            echo -e "  ${GREEN}./start_ecommerce_bot.sh start${NC}     - 启动机器人"
            echo -e ""
            echo -e "${YELLOW}推荐步骤：${NC}"
            echo -e "  1. ./start_ecommerce_bot.sh features   # 查看功能"
            echo -e "  2. ./start_ecommerce_bot.sh install    # 安装配置"
            echo -e "  3. ./start_ecommerce_bot.sh start      # 启动机器人"
            ;;
    esac
}

# 给脚本执行权限
if [ ! -x "$0" ]; then
    chmod +x "$0"
fi

# 运行主函数
main "$@"