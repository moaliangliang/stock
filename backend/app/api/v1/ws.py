"""
WebSocket 端点 - 实时行情推送 + 价格驱动信号扫描
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from app.services.ws_manager import manager
from app.core.database import async_session_factory
from app.services.data_provider import arefresh_all_tickers

AsyncSessionLocal = async_session_factory

router = APIRouter()

# Price-driven signal scanning: track previous prices to detect changes
_prev_prices: Dict[str, float] = {}


@router.websocket("/ws/tickers")
async def ticker_websocket(websocket: WebSocket):
    """实时行情 WebSocket - 客户端连接后持续接收 ticker 推送"""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket 异常: %s", e)
        manager.disconnect(websocket)


async def broadcast_tickers():
    """后台任务：定期刷新行情，广播给 WebSocket 客户端，并触发价格驱动信号扫描"""
    global _prev_prices

    while True:
        try:
            t_start = asyncio.get_event_loop().time()
            await arefresh_all_tickers()
            t_refresh = asyncio.get_event_loop().time()

            async with AsyncSessionLocal() as db:
                from sqlalchemy import select
                from app.models.market_data import Ticker, SymbolInfo
                result = await db.execute(select(Ticker))
                tickers = result.scalars().all()

                # Detect price changes for signal scanning
                changed = []
                for t in tickers:
                    if t.last_price is None:
                        continue
                    new_price = float(t.last_price)
                    old_price = _prev_prices.get(t.symbol)
                    if old_price is None or abs(new_price - old_price) > 0.001:
                        changed.append((t.symbol, new_price))
                    _prev_prices[t.symbol] = new_price

                # Filter to watched A-stocks only, then resolve names
                if changed:
                    changed_symbols = [c[0] for c in changed]
                    name_result = await db.execute(
                        select(SymbolInfo.symbol, SymbolInfo.name).where(
                            SymbolInfo.symbol.in_(changed_symbols),
                            SymbolInfo.is_watched == True,
                            SymbolInfo.asset_type == "stock",
                            SymbolInfo.status == "active",
                        )
                    )
                    name_map = {row[0]: row[1] for row in name_result.all()}

                    if name_map:
                        symbols_with_names = list(name_map.items())
                        t_detect = asyncio.get_event_loop().time()
                        logger.info(
                            f"[PD-TIMING] price-refresh={t_refresh-t_start:.2f}s "
                            f"ticker-query+detect={t_detect-t_refresh:.2f}s | "
                            f"{len(changed)} changed → {len(symbols_with_names)} watched"
                        )

                        from app.services.price_driven_signals import check_signals_for_changed
                        asyncio.create_task(check_signals_for_changed(symbols_with_names))

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

        await asyncio.sleep(10)
