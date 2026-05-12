"""
WebSocket 连接管理器 - 管理客户端连接和数据广播
支持 Redis Pub/Sub 跨 Worker 广播
"""
import asyncio
import json
import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理 WebSocket 连接的生命周期，支持跨 Worker 广播"""

    def __init__(self):
        self.active: Set[WebSocket] = set()
        self._pubsub_task: asyncio.Task | None = None
        self._running = False

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        self._ensure_pubsub_listener()
        logger.info("WebSocket 客户端已连接，当前连接数: %d", len(self.active))

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
        logger.info("WebSocket 客户端已断开，当前连接数: %d", len(self.active))

    async def broadcast(self, message: dict):
        """广播消息给所有客户端 (通过 Redis Pub/Sub 跨 Worker)"""
        await self._publish_redis(message)

    async def _broadcast_local(self, message: dict):
        """直接广播给本地 WebSocket 连接"""
        dead: Set[WebSocket] = set()
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        if dead:
            self.active -= dead

    # ------------------------------------------------------------------
    # Redis Pub/Sub for cross-worker broadcast
    # ------------------------------------------------------------------

    async def _publish_redis(self, message: dict):
        """Publish message to Redis channel for other workers to pick up."""
        try:
            from app.core.redis import get_sync_redis
            r = get_sync_redis()
            r.publish("ws:broadcast", json.dumps(message, default=str))
        except Exception:
            pass  # Redis unavailable — broadcast remains local only

    def _ensure_pubsub_listener(self):
        """Start the Redis Pub/Sub listener in background if not already running."""
        if self._running:
            return
        self._running = True
        try:
            self._pubsub_task = asyncio.ensure_future(self._listen_redis())
        except Exception:
            self._running = False

    async def _listen_redis(self):
        """Subscribe to Redis Pub/Sub and relay messages to local connections."""
        try:
            from app.core.redis import get_redis
            r = await get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe("ws:broadcast")
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    try:
                        msg = json.loads(raw["data"])
                        await self._broadcast_local(msg)
                    except Exception:
                        pass
        except Exception:
            self._running = False


manager = ConnectionManager()
