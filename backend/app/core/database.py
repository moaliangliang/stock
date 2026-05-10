"""
数据库引擎与会话管理 - 异步 SQLAlchemy
"""
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# 创建异步引擎（SQLite 设置 WAL 模式和忙等待超时，避免并发锁）
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    connect_args={"timeout": 15},
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record):
    """启用 SQLite WAL 模式，允许读写并发"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=15000")
    cursor.close()

# 异步会话工厂
async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
AsyncSessionLocal = async_session_factory  # 别名，用于 WebSocket 等非 dep 场景

# 同步引擎（用于 Celery 任务等异步不可用的场景）
from sqlalchemy import create_engine

sync_engine = create_engine(
    settings.DATABASE_URL.replace("+aiosqlite", "").replace("+asyncpg", ""),
    echo=settings.DEBUG,
    pool_pre_ping=True,
)
SyncSessionLocal = sessionmaker(bind=sync_engine)


class Base(DeclarativeBase):
    """声明性基类"""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话的依赖注入"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """初始化数据库表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
