"""
WebSocket 连接管理器 - 管理客户端连接和数据广播
"""
import json
import asyncio
import logging
from typing import Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理 WebSocket 连接的生命周期"""

    def __init__(self):
        self.active: Set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)
        logger.info("WebSocket 客户端已连接，当前连接数: %d", len(self.active))

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)
        logger.info("WebSocket 客户端已断开，当前连接数: %d", len(self.active))

    async def broadcast(self, message: dict):
        """广播消息给所有连接的客户端，自动清理断开的连接"""
        dead: Set[WebSocket] = set()
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        if dead:
            self.active -= dead


manager = ConnectionManager()
