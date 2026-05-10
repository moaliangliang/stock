"""
简易内存限流中间件 — 基于 IP 的请求频率限制
"""
import time
from collections import defaultdict
from typing import Dict, List, Tuple

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse


class RateLimiterMiddleware:
    """按 IP 限流：每窗口内最大请求数"""

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
        self._clients: Dict[str, List[float]] = defaultdict(list)

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
        history = self._clients[client_ip]

        # 清理窗口之前的记录
        cutoff = now - self.window_seconds
        self._clients[client_ip] = [t for t in history if t > cutoff]

        if len(self._clients[client_ip]) >= self.max_requests:
            response = JSONResponse(
                status_code=429,
                content={"code": 429, "message": "请求过于频繁，请稍后再试", "data": None},
            )
            await response(scope, receive, send)
            return

        self._clients[client_ip].append(now)
        await self.app(scope, receive, send)
