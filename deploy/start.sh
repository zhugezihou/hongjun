#!/usr/bin/env bash
# 鸿钧 · 一键部署脚本
# =====================
#
# 用法：
#   ./deploy/start.sh           # 前台运行
#   ./deploy/start.sh -d        # 后台运行
#   ./deploy/start.sh --docker  # Docker 模式
#   ./deploy/start.sh --systemd # Systemd 模式（本地）
#
# 支持三种部署模式：
#   1. 直接运行（开发模式）
#   2. Docker Compose（生产推荐）
#   3. Systemd（服务器部署）

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
MODE=""

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# 解析参数
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--docker)
            MODE="docker"
            shift
            ;;
        -s|--systemd)
            MODE="systemd"
            shift
            ;;
        -f|--foreground)
            MODE="foreground"
            shift
            ;;
        -h|--help)
            echo "用法: $0 [-d|--docker] [-s|--systemd] [-f|--foreground]"
            echo "  -d, --docker    Docker Compose 模式"
            echo "  -s, --systemd   Systemd 服务模式"
            echo "  -f, --foreground  前台运行（默认开发模式）"
            exit 0
            ;;
        *)
            error "未知参数: $1"
            ;;
    esac
done

# 检测模式
if [[ -z "$MODE" ]]; then
    if command -v docker &> /dev/null && docker compose version &> /dev/null; then
        MODE="docker"
    elif command -v systemctl &> /dev/null; then
        MODE="systemd"
    else
        MODE="foreground"
    fi
fi

cd "$PROJECT_ROOT"

case "$MODE" in
    docker)
        info "Docker Compose 模式启动..."
        if [[ ! -f ".env" ]]; then
            warn ".env 文件不存在，创建模板..."
            cat > .env << 'EOF'
# 鸿钧环境变量
TAVILY_API_KEY=tvly-your-key-here
OPENAI_API_KEY=sk-your-key-here
HONGJUN_MODE=production
SIX_MINISTRIES_URL=http://localhost:20002
EOF
            warn "请编辑 .env 文件填入你的 API Key"
        fi
        docker compose -f deploy/docker-compose.yaml up -d
        info "鸿钧已启动（Docker Compose）"
        info "查看日志: docker compose -f deploy/docker-compose.yaml logs -f"
        ;;

    systemd)
        info "Systemd 模式部署..."
        # 安装 service 文件
        SERVICE_FILE="$SCRIPT_DIR/hongjun.service"
        if [[ -f "$SERVICE_FILE" ]]; then
            sudo cp "$SERVICE_FILE" /etc/systemd/system/hongjun.service
            sudo systemctl daemon-reload
            sudo systemctl enable hongjun
            sudo systemctl restart hongjun
            info "鸿钧已启动（systemd）"
            systemctl status hongjun --no-pager || true
        else
            error "Service 文件不存在: $SERVICE_FILE"
        fi
        ;;

    foreground)
        info "前台运行（开发模式）..."
        PYTHONPATH="$PROJECT_ROOT/src" python3 src/hongjun.py
        ;;
esac
