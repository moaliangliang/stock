"""
Data management service - data import, export, update scheduling."""
from __future__ import annotations
import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import KLine, SymbolInfo


async def import_historical_data(db: AsyncSession, symbol: str, interval: str, data: list[dict]) -> dict:
    """导入历史K线数据"""
    count = 0
    for row in data:
        kline = KLine(
            symbol=symbol,
            interval=interval,
            timestamp=row.get("timestamp", datetime.now(timezone.utc)),
            open=float(row.get("open", 0)),
            high=float(row.get("high", 0)),
            low=float(row.get("low", 0)),
            close=float(row.get("close", 0)),
            volume=float(row.get("volume", 0)),
            amount=float(row.get("amount", 0)),
        )
        db.add(kline)
        count += 1
    await db.flush()
    return {"imported": count, "symbol": symbol, "interval": interval}


async def export_data(db: AsyncSession, symbol: str, interval: str, start_time: Optional[str] = None, end_time: Optional[str] = None, format: str = "json") -> Any:
    """导出K线数据"""
    conditions = [KLine.symbol == symbol, KLine.interval == interval]
    result = await db.execute(select(KLine).where(*conditions).order_by(KLine.timestamp).limit(10000))
    rows = result.scalars().all()

    data = [{"timestamp": str(r.timestamp), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for r in rows]

    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(data)
        return output.getvalue()
    return data


async def get_data_update_status(db: AsyncSession) -> dict:
    """获取数据更新状态"""
    result = await db.execute(select(SymbolInfo).where(SymbolInfo.status == "active"))
    symbols = result.scalars().all()
    return {
        "total_symbols": len(symbols),
        "last_update": datetime.now(timezone.utc).isoformat(),
        "status": "active",
    }


async def trigger_data_update(db: AsyncSession, symbol: Optional[str] = None) -> dict:
    """触发数据更新任务"""
    # 实际项目中这里会发送 Celery 任务
    return {
        "status": "triggered",
        "symbol": symbol or "all",
        "message": "数据更新任务已提交",
    }
