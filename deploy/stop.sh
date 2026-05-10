#!/usr/bin/env bash
# 鸿钧 · 停止脚本
# =================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

stop_docker() {
    info "停止 Docker Compose..."
    docker compose -f "$PROJECT_ROOT/deploy/docker-compose.yaml" down 2>/dev/null || true
}

stop_systemd() {
    info "停止 Systemd 服务..."
    sudo systemctl stop hongjun 2>/dev/null || true
    sudo systemctl disable hongjun 2>/dev/null || true
}

stop_direct() {
    info "停止hongjun进程..."
    pkill -f "hongjun.py" 2>/dev/null || true
}

# 检测运行模式并停止
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "hongjun"; then
    stop_docker
elif systemctl is-active --quiet hongjun 2>/dev/null; then
    stop_systemd
else
    stop_direct
fi

info "鸿钧已停止"
