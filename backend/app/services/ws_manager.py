"""
WebSocket 连接管理器 - 管理客户端连接和数据广播
支持 Redis Pub/Sub 跨 Worker 广播 + per-client symbol subscriptions
"""
import asyncio
import json
import logging
from typing import Dict, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理 WebSocket 连接的生命周期，支持跨 Worker 广播和按标的订阅。

    Clients can subscribe to specific symbols via:
        {"type": "subscribe", "symbols": ["000001.SZ", "600519.SH"]}
    or subscribe to ALL tickers with:
        {"type": "subscribe", "symbols": ["*"]}

    Broadcast messages are filtered per-client: only tickers matching the
    client's subscription are sent. Non-ticker messages are always delivered.
    """

    def __init__(self):
        self.active: Set[WebSocket] = set()
        self._subscriptions: Dict[WebSocket, Set[str]] = {}
        self._pubsub_task: asyncio.Task | None = None
        self._running = False

    async def connect(self, ws: WebSocket):
        self.active.add(ws)
        self._ensure_pubsub_listener()

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
        self._subscriptions.pop(ws, None)
        logger.info("WebSocket 客户端已断开，当前连接数: %d", len(self.active))

    def subscribe(self, ws: WebSocket, symbols: list[str]):
        """Set client subscription. Empty list or ['*'] = all tickers."""
        if not symbols or "*" in symbols:
            self._subscriptions.pop(ws, None)  # None = wildcard (all tickers)
        else:
            self._subscriptions[ws] = set(symbols)
        logger.info("WebSocket 订阅更新: %s → %s", id(ws), symbols[:5] if symbols else "all")

    async def broadcast(self, message: dict):
        """广播消息给所有客户端 (通过 Redis Pub/Sub 跨 Worker)"""
        await self._publish_redis(message)

    async def _broadcast_local(self, message: dict):
        """直接广播给本地 WebSocket 连接，按订阅过滤 ticker 消息。"""
        dead: Set[WebSocket] = set()
        msg_type = message.get("type", "")
        data = message.get("data", [])

        for ws in self.active:
            try:
                # Apply subscription filter for ticker messages
                if msg_type == "ticker" and data:
                    subs = self._subscriptions.get(ws)
                    if subs is not None:  # None = wildcard (all), present = filtered
                        filtered_data = [t for t in data if t.get("symbol") in subs]
                        if not filtered_data:
                            continue
                        await ws.send_json({**message, "data": filtered_data})
                    else:
                        await ws.send_json(message)
                else:
                    await ws.send_json(message)
            except Exception:
                dead.add(ws)
        if dead:
            for ws in dead:
                self.active.discard(ws)
                self._subscriptions.pop(ws, None)

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
