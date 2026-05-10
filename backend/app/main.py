"""
FastAPI 主应用 - 全功能量化交易平台后端入口
"""
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.core.config import settings
from app.core.database import init_db, engine
from app.utils.logger import setup_logger
from app.utils.token_usage import get_stats as get_token_usage_stats

# 导入路由
from app.api.v1.endpoints import auth, market, strategy, backtest, trade, risk, data, notification, alert, decision, report
from app.api.v1 import ws as ws_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    setup_logger()

    # 安全校验：SECRET_KEY 为空则拒绝启动
    if not settings.SECRET_KEY:
        logger.error("❌ SECRET_KEY 未配置，请设置环境变量或在 .env 中配置")
        raise RuntimeError("SECRET_KEY 不能为空，拒绝启动以保护系统安全")

    logger.info(f"🚀 {settings.APP_NAME} v{settings.APP_VERSION} 启动中...")
    await init_db()
    logger.info("✅ 数据库初始化完成")
    # 启动 WebSocket 行情推送后台任务
    task = asyncio.create_task(ws_router.broadcast_tickers())
    logger.info("✅ WebSocket 行情推送已启动")
    yield
    # 关闭时
    task.cancel()
    await engine.dispose()
    logger.info("👋 应用已关闭")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="全功能量化交易平台 API",
    lifespan=lifespan,
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 限流（仅生产环境生效，DEBUG 模式不限流）
if not settings.DEBUG:
    from app.utils.rate_limiter import RateLimiterMiddleware
    app.add_middleware(RateLimiterMiddleware, max_requests=60, window_seconds=60)


# 全局异常处理
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """参数校验错误 — 返回 422"""
    logger.warning(f"参数校验失败: {exc.errors()} | 路径: {request.url.path}")
    return JSONResponse(
        status_code=422,
        content={"code": 422, "message": "参数校验失败", "detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局未捕获异常处理 — 返回 500"""
    logger.error(f"未捕获异常: {exc} | 路径: {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"code": 500, "message": f"服务器内部错误: {str(exc)}", "data": None},
    )


# 注册路由
app.include_router(auth.router, prefix="/api/v1")
app.include_router(market.router, prefix="/api/v1")
app.include_router(strategy.router, prefix="/api/v1")
app.include_router(backtest.router, prefix="/api/v1")
app.include_router(trade.router, prefix="/api/v1")
app.include_router(risk.router, prefix="/api/v1")
app.include_router(data.router, prefix="/api/v1")
app.include_router(ws_router.router, prefix="/api/v1")
app.include_router(notification.router, prefix="/api/v1")
app.include_router(alert.router, prefix="/api/v1")
app.include_router(decision.router, prefix="/api/v1")
app.include_router(report.router, prefix="/api/v1")


@app.get("/api/v1/health")
async def health_check():
    """健康检查"""
    return {
        "code": 200,
        "message": "success",
        "data": {
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "status": "running",
        },
    }


@app.get("/api/v1/system/token-stats")
async def token_stats():
    """Skills API Token 使用量统计"""
    all_stats = get_token_usage_stats()
    # Ensure skills_api entry always exists for the frontend
    if isinstance(all_stats, list):
        if not any(s.get("api_name") == "skills_api" for s in all_stats):
            all_stats.append(get_token_usage_stats("skills_api"))
    return {
        "code": 200,
        "message": "success",
        "data": all_stats,
    }


@app.get("/")
async def root():
    """根路径 - API信息"""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "api_prefix": "/api/v1",
    }
