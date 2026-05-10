"""
行情接口 - K线数据、实时行情、标的信息"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.market_data import KLineResponse, TickerResponse, SymbolInfoResponse
from app.schemas.common import Response
from app.services.market import (
    get_kline_data,
    save_kline_data,
    get_ticker,
    get_all_tickers,
    get_symbols,
    mock_market_data,
    ensure_mock_tickers,
)
from app.services.data_provider import arefresh_all_tickers, arefresh_ticker, afetch_real_klines
from app.models.market_data import KLine, SymbolInfo, Ticker
from app.models.user import User
from sqlalchemy import select, func

router = APIRouter(prefix="/market", tags=["行情中心"])


# 各周期对应的模拟数据生成天数（短周期生成较少数据，避免耗时过长）
INTERVAL_DAYS = {
    "1m": 1,
    "5m": 3,
    "15m": 7,
    "30m": 14,
    "60m": 30,
    "1d": 90,
}

INTERVAL_MINUTES = {
    "1d": 1440,
    "60m": 60,
    "30m": 30,
    "15m": 15,
    "5m": 5,
    "1m": 1,
}


async def _ensure_mock_data(db: AsyncSession, symbol: str, interval: str):
    """确保K线数据存在。仅在 mock 模式下生成模拟数据，真实模式下不做降级。"""
    from app.core.config import settings
    from app.core.market_constants import MOCK_CONFIG

    # 如果是真实数据源，检查是否有真实数据，没有则直接返回（不降级到 mock）
    if settings.MARKET_DATA_PROVIDER not in ("mock", ""):
        count_result = await db.execute(
            select(func.count()).select_from(KLine).where(
                KLine.symbol == symbol, KLine.interval == interval
            )
        )
        existing_count = count_result.scalar()
        if existing_count and existing_count > 0:
            return

        import logging
        logging.getLogger(__name__).warning(
            "%s:%s 无K线数据，当前数据源=%s，不降级到mock。请使用 /market/refresh-klines 拉取真实数据",
            symbol, interval, settings.MARKET_DATA_PROVIDER,
        )
        return

    # 检查最新一条数据的时间
    result = await db.execute(
        select(KLine.timestamp)
        .where(KLine.symbol == symbol, KLine.interval == interval)
        .order_by(KLine.timestamp.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    # 确保 latest 是 offset-aware（SQLite 可能丢失时区信息）
    if latest and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)

    interval_minutes = INTERVAL_MINUTES.get(interval, 60)
    # 日线必须是最新（6小时内）；短周期保持原有2倍周期检查
    if interval == "1d":
        if latest and (now - latest).total_seconds() < 21600:
            return
    else:
        if latest and (now - latest).total_seconds() < interval_minutes * 60 * 2:
            return

    config = MOCK_CONFIG.get(symbol, {"base_price": 100.0, "days": 90, "interval_minutes": 60})
    days = INTERVAL_DAYS.get(interval, config["days"])

    # Use current ticker price as base so klines are up-to-date
    ticker_result = await db.execute(
        select(Ticker.last_price).where(Ticker.symbol == symbol)
    )
    ticker_price = ticker_result.scalar_one_or_none()
    base_price = float(ticker_price) if ticker_price else config["base_price"]

    # 删除旧数据后重新生成
    await db.execute(KLine.__table__.delete().where(KLine.symbol == symbol, KLine.interval == interval))
    await db.flush()

    data = mock_market_data(
        base_price=base_price,
        days=days,
        interval_minutes=interval_minutes,
        volatility=0.03,
    )
    await save_kline_data(db, symbol, interval, data)


@router.get("/klines", response_model=Response[List[KLineResponse]])
async def kline_data(
    symbol: str = Query(..., description="标的代码"),
    interval: str = Query("1d", description="时间周期"),
    start_time: Optional[str] = Query(None, description="开始时间"),
    end_time: Optional[str] = Query(None, description="结束时间"),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取K线数据（首次查询自动生成模拟数据）"""
    # 转换字符串时间为 datetime
    start_dt = None
    end_dt = None
    if start_time:
        try:
            start_dt = datetime.fromisoformat(start_time)
        except ValueError:
            pass
    if end_time:
        try:
            end_dt = datetime.fromisoformat(end_time)
        except ValueError:
            pass

    await _ensure_mock_data(db, symbol, interval)
    data = await get_kline_data(db, symbol, interval, start_dt, end_dt, limit)
    return Response(data=data)


async def _enrich_ticker_name(db: AsyncSession, tickers: list):
    """为行情数据附加标的名称"""
    result = await db.execute(select(SymbolInfo.symbol, SymbolInfo.name))
    name_map = {row.symbol: row.name for row in result.all()}
    for t in tickers:
        t.name = name_map.get(t.symbol, "")


@router.get("/tickers", response_model=Response[List[TickerResponse]])
async def tickers(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取实时行情列表"""
    await ensure_mock_tickers(db)
    await arefresh_all_tickers(db)
    data = await get_all_tickers(db)
    data = data[skip:skip + limit]
    await _enrich_ticker_name(db, data)
    return Response(data=data)


@router.get("/ticker/{symbol}", response_model=Response[TickerResponse])
async def ticker(
    symbol: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个标的行情（每次刷新价格波动）"""
    await ensure_mock_tickers(db)
    await arefresh_ticker(db, symbol)
    data = await get_ticker(db, symbol)
    if data:
        await _enrich_ticker_name(db, [data])
    return Response(data=data)


@router.post("/refresh-klines", response_model=Response[dict])
async def refresh_klines(
    symbol: str = Query(..., description="标的代码"),
    interval: str = Query("1d", description="时间周期"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """通过 akshare 拉取真实 K 线数据（仅支持 A 股日线）"""
    if not symbol.endswith((".SH", ".SZ")):
        return Response(data={"message": "仅支持A股标的", "inserted": 0})
    data = await afetch_real_klines(symbol, interval)
    if not data:
        return Response(data={"message": "akshare 数据不可用，请稍后重试", "inserted": 0})
    inserted = await save_kline_data(db, symbol, interval, data)
    return Response(data={"message": f"已更新 {inserted} 条K线数据", "inserted": inserted})


@router.get("/symbols", response_model=Response[List[SymbolInfoResponse]])
async def symbols(
    asset_type: Optional[str] = Query(None, description="资产类型"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """获取标的信息列表"""
    data = await get_symbols(db, asset_type)
    data = data[skip:skip + limit]
    return Response(data=data)


@router.put("/symbols/{symbol}/watched", response_model=Response[dict])
async def toggle_watched(
    symbol: str,
    db: AsyncSession = Depends(get_db),
):
    """切换标的的自选状态"""
    from sqlalchemy import update
    from app.models.market_data import SymbolInfo
    row = await db.execute(
        select(SymbolInfo).where(SymbolInfo.symbol == symbol)
    )
    sym = row.scalar_one_or_none()
    if not sym:
        return Response(code=404, message="标的不存在")
    new_val = not sym.is_watched
    await db.execute(
        update(SymbolInfo)
        .where(SymbolInfo.symbol == symbol)
        .values(is_watched=new_val)
    )
    await db.commit()
    return Response(data={"symbol": symbol, "is_watched": new_val})
