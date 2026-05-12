"""
回测接口 - 执行回测、回测历史查询"""
from __future__ import annotations
import hashlib
import json
import time
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.strategy import BacktestRequest, BacktestResult
from app.schemas.common import Response
from app.services.backtest import run_backtest, get_backtest_history, get_backtest_by_id
from app.services.market import get_kline_data
from app.models.user import User
from app.models.strategy import Strategy

router = APIRouter(prefix="/backtest", tags=["回测分析"])

# In-memory backtest result cache (TTL = 5 minutes)
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # seconds


def _cache_key(req: BacktestRequest) -> str:
    raw = f"{req.strategy_id}|{req.symbol}|{req.interval}|{req.start_date}|{req.end_date}|{req.initial_capital}"
    return hashlib.md5(raw.encode()).hexdigest()


@router.post("/run", response_model=Response[BacktestResult])
async def backtest_run(
    req: BacktestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """执行回测（带5分钟缓存）"""
    # Check cache
    ck = _cache_key(req)
    if ck in _cache:
        cached_at, cached_result = _cache[ck]
        if time.time() - cached_at < _CACHE_TTL:
            return Response(data=cached_result, message="回测执行完成(cached)")

    # 获取策略
    stmt = select(Strategy).where(Strategy.id == req.strategy_id)
    result_set = await db.execute(stmt)
    strategy = result_set.scalar_one_or_none()
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if strategy.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权使用该策略")

    # 获取K线数据
    kline_objs = await get_kline_data(db, req.symbol, req.interval)
    kline_data = [
        {
            "timestamp": int(k.timestamp.timestamp()),  # Unix timestamp for backtest
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume,
            "amount": k.amount,
        }
        for k in kline_objs
    ]

    # 执行回测
    result = run_backtest(
        strategy_type=strategy.type,
        params=strategy.params or {},
        kline_data=kline_data,
        initial_capital=req.initial_capital,
        commission=req.commission,
        slippage=req.slippage,
    )
    # Cache result
    _cache[ck] = (time.time(), result)
    # Limit cache size
    if len(_cache) > 200:
        _cache.pop(next(iter(_cache)))
    return Response(data=result, message="回测执行完成")


@router.get("/history", response_model=Response[List[dict]])
async def backtest_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    strategy_id: Optional[int] = Query(None, description="按策略ID过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取回测历史记录"""
    records = await get_backtest_history(db, current_user.id, skip=skip, limit=limit, strategy_id=strategy_id)
    return Response(data=records)


@router.get("/{backtest_id}", response_model=Response[dict])
async def backtest_detail(
    backtest_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取回测详情"""
    record = await get_backtest_by_id(db, backtest_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="回测记录不存在")
    return Response(data=record)
