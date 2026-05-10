#!/bin/bash
# ==========================================
# 量化交易平台 - 本地开发环境启动脚本
# ==========================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=========================================="
echo "  量化交易平台 - 开发环境启动"
echo "=========================================="

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "❌ 请先安装 Python 3.10+"
    exit 1
fi

# 检查 Node.js
if ! command -v node &> /dev/null; then
    echo "❌ 请先安装 Node.js 18+"
    exit 1
fi

# 检查 Docker (可选)
DOCKER_AVAILABLE=false
if command -v docker &> /dev/null; then
    DOCKER_AVAILABLE=true
fi

# 后端环境变量
if [ ! -f "$PROJECT_DIR/backend/.env" ]; then
    echo "📝 创建后端 .env 配置..."
    cp "$PROJECT_DIR/backend/.env.example" "$PROJECT_DIR/backend/.env"
    echo "✅ 已创建 .env，请根据需要修改配置"
fi

# 启动数据库 (Docker)
if [ "$DOCKER_AVAILABLE" = true ]; then
    echo "🐳 启动 PostgreSQL 和 Redis..."
    cd "$PROJECT_DIR"
    docker-compose up -d postgres redis
    echo "⏳ 等待数据库就绪..."
    sleep 3
fi

# 安装后端依赖
echo "📦 安装后端 Python 依赖..."
cd "$PROJECT_DIR/backend"
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt -q

# 安装前端依赖
echo "📦 安装前端 Node.js 依赖..."
cd "$PROJECT_DIR/frontend"
if [ ! -d "node_modules" ]; then
    npm install
fi

# 启动后端
echo "🚀 启动后端服务 (端口 8000)..."
cd "$PROJECT_DIR/backend"
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# 启动前端
echo "🚀 启动前端开发服务器 (端口 5173)..."
cd "$PROJECT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "=========================================="
echo "  ✅ 启动完成!"
echo "=========================================="
echo "  后端 API:  http://localhost:8000"
echo "  API 文档:  http://localhost:8000/docs"
echo "  前端页面:  http://localhost:5173"
echo ""
echo "  按 Ctrl+C 停止所有服务"
echo "=========================================="

# 捕获退出信号
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM

wait
