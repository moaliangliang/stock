"""
Redis 客户端管理 - 缓存行情、策略状态、任务锁
"""
import json
from typing import Optional
from redis.asyncio import Redis as AsyncRedis, ConnectionPool

from app.core.config import settings

_pool: Optional[ConnectionPool] = None
_redis_client: Optional[AsyncRedis] = None


async def get_redis() -> AsyncRedis:
    global _redis_client, _pool
    if _redis_client is None:
        _pool = ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_client = AsyncRedis(connection_pool=_pool)
    return _redis_client


async def close_redis():
    global _redis_client, _pool
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
    if _pool:
        await _pool.disconnect()
        _pool = None


async def cache_kline(symbol: str, interval: str, data: list, expire: int = 3600):
    r = await get_redis()
    key = f"kline:{symbol}:{interval}"
    await r.set(key, json.dumps(data, default=str), ex=expire)


async def get_cached_kline(symbol: str, interval: str):
    r = await get_redis()
    key = f"kline:{symbol}:{interval}"
    data = await r.get(key)
    return json.loads(data) if data else None


async def cache_ticker(symbol: str, data: dict, expire: int = 60):
    r = await get_redis()
    key = f"ticker:{symbol}"
    await r.set(key, json.dumps(data, default=str), ex=expire)


async def get_cached_ticker(symbol: str) -> Optional[dict]:
    r = await get_redis()
    key = f"ticker:{symbol}"
    data = await r.get(key)
    return json.loads(data) if data else None


async def acquire_lock(lock_name: str, timeout: int = 10) -> bool:
    r = await get_redis()
    key = f"lock:{lock_name}"
    return await r.setnx(key, "locked") and await r.expire(key, timeout)


async def release_lock(lock_name: str):
    r = await get_redis()
    await r.delete(f"lock:{lock_name}")
