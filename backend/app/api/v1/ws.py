"""
WebSocket 端点 - 实时行情推送
"""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.ws_manager import manager
from app.core.database import async_session_factory
from app.services.data_provider import arefresh_all_tickers

AsyncSessionLocal = async_session_factory

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/tickers")
async def ticker_websocket(websocket: WebSocket):
    """实时行情 WebSocket - 客户端连接后持续接收 ticker 推送"""
    await manager.connect(websocket)
    try:
        while True:
            # 接收客户端消息（保持连接活跃）
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket 异常: %s", e)
        manager.disconnect(websocket)


async def broadcast_tickers():
    """后台任务：定期刷新行情并广播给所有 WebSocket 客户端"""
    while True:
        try:
            await arefresh_all_tickers()

            # 从数据库读取最新行情并广播
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select
                from app.models.market_data import Ticker
                result = await db.execute(select(Ticker))
                tickers = result.scalars().all()

            if manager.active:
                msg = {
                    "type": "ticker",
                    "data": [
                        {
                            "symbol": t.symbol,
                            "last_price": t.last_price,
                            "change_24h": t.change_24h,
                            "high_24h": t.high_24h,
                            "low_24h": t.low_24h,
                            "volume_24h": t.volume_24h,
                            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
                        }
                        for t in tickers
                    ],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                await manager.broadcast(msg)
        except Exception as e:
            logger.error("广播行情失败: %s", e)

        await asyncio.sleep(10)  # 每 10 秒推送一次
