"""
数据管理接口 - 数据导入、导出、更新状态"""
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user, get_current_active_superuser
from app.schemas.common import Response
from app.models.user import User
from app.services.data import (
    import_historical_data,
    export_data,
    get_data_update_status,
    trigger_data_update,
)

router = APIRouter(prefix="/data", tags=["数据管理"])


@router.post("/import", response_model=Response[dict])
async def data_import(
    symbol: str = Query(..., description="标的代码"),
    exchange: str = Query(..., description="交易所"),
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    interval: str = Query("1d", description="时间周期"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
):
    """导入历史数据（管理员权限）"""
    result = await import_historical_data(db, symbol=symbol, exchange=exchange,
                                          start_date=start_date, end_date=end_date,
                                          interval=interval)
    return Response(data=result, message="数据导入完成")


@router.get("/export", response_model=Response[dict])
async def data_export(
    symbol: str = Query(..., description="标的代码"),
    interval: str = Query("1d", description="时间周期"),
    start_date: str = Query(..., description="开始日期 YYYY-MM-DD"),
    end_date: str = Query(..., description="结束日期 YYYY-MM-DD"),
    format: str = Query("csv", description="导出格式: csv/json"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
):
    """导出数据（管理员权限）"""
    result = await export_data(db, symbol=symbol, interval=interval,
                               start_date=start_date, end_date=end_date,
                               export_format=format)
    return Response(data=result)


@router.get("/status", response_model=Response[dict])
async def data_status(
    symbol: Optional[str] = Query(None, description="标的代码"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取数据更新状态"""
    status_info = await get_data_update_status(db, symbol=symbol)
    return Response(data=status_info)


@router.post("/update", response_model=Response[dict])
async def data_update(
    symbols: List[str] = Query(..., description="需要更新数据的标的代码列表"),
    intervals: List[str] = Query(["1d"], description="时间周期列表"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_superuser),
):
    """触发数据更新任务（管理员权限）"""
    result = await trigger_data_update(db, symbols=symbols, intervals=intervals)
    return Response(data=result, message="数据更新任务已触发")
