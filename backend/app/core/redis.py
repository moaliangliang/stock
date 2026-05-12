"""
Redis 客户端管理 - 缓存行情、策略状态、任务锁
"""
import json
import uuid
from typing import Optional, Tuple
from redis.asyncio import Redis as AsyncRedis, ConnectionPool
from redis import Redis

from app.core.config import settings

_pool: Optional[ConnectionPool] = None
_redis_client: Optional[AsyncRedis] = None
_sync_redis: Optional[Redis] = None


async def get_redis() -> AsyncRedis:
    global _redis_client, _pool
    if _redis_client is None:
        _pool = ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)
        _redis_client = AsyncRedis(connection_pool=_pool)
    return _redis_client


def get_sync_redis() -> Redis:
    """Synchronous Redis client for Celery tasks."""
    global _sync_redis
    if _sync_redis is None:
        _sync_redis = Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _sync_redis


async def close_redis():
    global _redis_client, _pool, _sync_redis
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
    if _pool:
        await _pool.disconnect()
        _pool = None
    if _sync_redis:
        _sync_redis.close()
        _sync_redis = None


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


async def acquire_lock(lock_name: str, timeout: int = 10) -> str | None:
    """Acquire a distributed lock via Redis SET NX EX (atomic).

    Returns a UUID token if the lock was acquired, None otherwise.
    Save the token and pass it to release_lock() to safely release.
    The lock auto-expires after *timeout* seconds to prevent deadlocks.
    """
    import uuid
    r = await get_redis()
    key = f"lock:{lock_name}"
    token = str(uuid.uuid4())
    acquired = await r.set(key, token, nx=True, ex=timeout)
    return token if acquired else None


async def release_lock(lock_name: str, token: str):
    """Release a distributed lock. Only releases if *token* matches the
    lock's current value (ownership check via Lua script)."""
    r = await get_redis()
    key = f"lock:{lock_name}"
    script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    await r.eval(script, 1, key, token)


def acquire_sync_lock(lock_name: str, timeout: int = 120) -> str | None:
    """Acquire a distributed lock via sync Redis SET NX EX (atomic).

    For use in Celery tasks. Returns a UUID token if acquired, None otherwise.
    Save the token and pass it to release_sync_lock() to safely release.
    The lock auto-expires to prevent deadlocks if a worker crashes.
    """
    import uuid
    r = get_sync_redis()
    key = f"lock:{lock_name}"
    token = str(uuid.uuid4())
    acquired = bool(r.set(key, token, nx=True, ex=timeout))
    return token if acquired else None


def release_sync_lock(lock_name: str, token: str):
    """Release a distributed lock acquired via acquire_sync_lock.
    Only releases if *token* matches the lock's current value."""
    r = get_sync_redis()
    key = f"lock:{lock_name}"
    script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    r.eval(script, 1, key, token)


class TaskLock:
    """Context manager for Celery task distributed locking.

    Usage:
        with TaskLock("my_task", timeout=120) as acquired:
            if not acquired:
                return {"skipped": "another instance is running"}
            # ... task body ...
    """

    def __init__(self, task_name: str, timeout: int = 120):
        self.task_name = task_name
        self.timeout = timeout
        self._token: str | None = None

    def __enter__(self):
        self._token = acquire_sync_lock(self.task_name, self.timeout)
        return self._token is not None

    def __exit__(self, *args):
        if self._token:
            release_sync_lock(self.task_name, self._token)
        return False
