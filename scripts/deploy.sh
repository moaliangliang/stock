#!/bin/bash
# ============================================================
# 量化交易平台 - 生产环境部署脚本
# 用法: bash scripts/deploy.sh [--skip-build] [--skip-check]
# ============================================================
set -e

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

SKIP_BUILD=false
SKIP_CHECK=false

for arg in "$@"; do
    case "$arg" in
        --skip-build) SKIP_BUILD=true ;;
        --skip-check) SKIP_CHECK=true ;;
    esac
done

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

echo "=========================================="
echo "  量化交易平台 - 生产环境部署"
echo "=========================================="
echo ""

# ---------- 预检 ----------
if [ "$SKIP_CHECK" != true ]; then
    log "执行部署前检查..."

    # Docker
    if ! command -v docker &> /dev/null; then
        err "Docker 未安装，请先安装 Docker"
        exit 1
    fi

    # Docker Compose
    if docker compose version &> /dev/null; then
        COMPOSE="docker compose"
    elif command -v docker-compose &> /dev/null; then
        COMPOSE="docker-compose"
    else
        err "未找到 docker compose"
        exit 1
    fi

    # .env 文件
    if [ ! -f backend/.env ]; then
        warn "backend/.env 不存在，从 .env.example 创建..."
        cp backend/.env.example backend/.env
        warn "请编辑 backend/.env，将所有 <CHANGE_ME> 替换为实际值，然后重新运行部署"
        exit 1
    fi

    # 检查 <CHANGE_ME> 是否已修改
    if grep -q "<CHANGE_ME>" backend/.env; then
        err "backend/.env 中存在未修改的占位符 <CHANGE_ME>，请先配置"
        exit 1
    fi

    # 检查 DEBUG
    if grep -q "DEBUG=true" backend/.env; then
        warn "DEBUG=true 仍在开发模式，生产环境建议设为 false"
    fi

    # SSL 证书
    if [ ! -f certs/nginx.crt ] || [ ! -f certs/nginx.key ]; then
        warn "SSL 证书不存在，正在生成自签名证书..."
        mkdir -p certs
        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout certs/nginx.key \
            -out certs/nginx.crt \
            -subj "/C=CN/ST=Shanghai/L=Shanghai/O=QuantTrade/CN=localhost" 2>/dev/null
        log "自签名证书已生成（生产环境请替换为正式证书）"
    fi

    log "预检通过"
else
    COMPOSE="docker compose" 2>/dev/null || COMPOSE="docker-compose"
fi

# ---------- 构建前端 ----------
if [ "$SKIP_BUILD" != true ]; then
    log "构建前端..."
    cd frontend
    npm install --silent
    npm run build
    cd ..
    log "前端构建完成"
fi

# ---------- 停止旧服务 ----------
log "停止旧服务..."
$COMPOSE down 2>/dev/null || true

# ---------- 启动服务 ----------
log "启动所有服务..."
$COMPOSE up -d --build

# ---------- 等待健康检查 ----------
log "等待服务就绪..."
sleep 5

# ---------- 运行数据库迁移 ----------
log "运行数据库迁移..."
$COMPOSE exec -T backend alembic upgrade head 2>/dev/null || warn "Alembic 迁移跳过（表可能已存在）"

# ---------- 播种初始数据 ----------
log "播种初始数据..."
$COMPOSE exec -T backend python scripts/seed_data.py 2>/dev/null || warn "种子数据跳过（可能已存在）"

echo ""
echo "=========================================="
echo "  部署完成"
echo "=========================================="
echo ""
echo "  前端页面:  https://localhost"
echo "  API 文档:  https://localhost:8000/docs"
echo "  PostgreSQL: localhost:5432"
echo "  Redis:      localhost:6379"
echo ""
echo "  查看日志:   $COMPOSE logs -f [service]"
echo "  停止服务:   $COMPOSE down"
echo "  重启服务:   $COMPOSE restart [service]"
echo "=========================================="
