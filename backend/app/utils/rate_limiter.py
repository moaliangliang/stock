"""
Redis 滑动窗口限流中间件 — 基于 IP 的请求频率限制
多 Worker 共享 Redis，避免进程内字典导致的限流失效
"""
import time
from typing import Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class RateLimiterMiddleware:
    """基于 Redis 的滑动窗口限流器，跨 Worker 一致"""

    def __init__(
        self,
        app: FastAPI,
        max_requests: int = 60,
        window_seconds: int = 60,
        excluded_paths: Tuple[str, ...] = ("/api/v1/health", "/docs", "/openapi.json"),
    ):
        self.app = app
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.excluded_paths = excluded_paths

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path = request.url.path

        if path.startswith(self.excluded_paths):
            await self.app(scope, receive, send)
            return

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        key = f"ratelimit:{client_ip}"
        cutoff = now - self.window_seconds

        try:
            from app.core.redis import get_sync_redis
            r = get_sync_redis()
            pipe = r.pipeline()
            pipe.zremrangebyscore(key, 0, cutoff)   # remove old entries
            pipe.zcard(key)                          # count current window
            results = pipe.execute()
            current_count = results[1] if len(results) > 1 else 0

            if current_count >= self.max_requests:
                response = JSONResponse(
                    status_code=429,
                    content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None},
                )
                await response(scope, receive, send)
                return

            # Add current request timestamp and set expiry
            pipe = r.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.expire(key, self.window_seconds + 10)
            pipe.execute()

        except Exception:
            # Redis unavailable — fail closed: deny traffic to prevent abuse
            response = JSONResponse(
                status_code=503,
                content={"code": 503, "message": "服务暂时不可用，请稍后再试", "data": None},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
