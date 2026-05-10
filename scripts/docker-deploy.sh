#!/bin/bash
# ==========================================
# 量化交易平台 - Docker生产环境部署脚本
# ==========================================

set -e

echo "=========================================="
echo "  量化交易平台 - 生产环境部署"
echo "=========================================="

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "❌ 请先安装 Docker"
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

# 1. 构建前端
echo "📦 构建前端..."
cd frontend
npm install
npm run build
cd ..

# 2. 复制 .env
if [ ! -f backend/.env ]; then
    echo "📝 创建 .env 文件..."
    cp backend/.env.example backend/.env
    echo "⚠️  请修改 backend/.env 中的配置!"
fi

# 3. 启动所有服务
echo "🚀 启动 Docker 服务..."
docker-compose down 2>/dev/null || true
docker-compose up -d --build

echo ""
echo "=========================================="
echo "  ✅ 部署完成!"
echo "=========================================="
echo "  前端页面:  http://localhost:80"
echo "  API 文档:  http://localhost:8000/docs"
echo "  PostgreSQL: localhost:5432"
echo "  Redis:      localhost:6379"
echo ""
echo "  查看日志: docker-compose logs -f"
echo "  停止服务: docker-compose down"
echo "=========================================="
