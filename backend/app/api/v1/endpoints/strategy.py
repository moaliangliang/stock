"""
策略接口 - 策略管理、运行、日志、模板"""
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.strategy import StrategyCreate, StrategyUpdate, StrategyResponse, StrategyRunLogResponse
from app.schemas.common import Response
from app.services.strategy import (
    create_strategy,
    get_strategies,
    get_strategy_by_id,
    update_strategy,
    delete_strategy,
    run_strategy,
    get_strategy_logs,
    get_strategy_templates,
)
from app.models.user import User
from app.models.strategy import Strategy, StrategyRunLog

router = APIRouter(prefix="/strategies", tags=["策略中心"])


@router.post("", response_model=Response[StrategyResponse])
async def create_strategy_endpoint(
    req: StrategyCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建策略"""
    strategy = await create_strategy(db, current_user.id, req.model_dump())
    return Response(data=StrategyResponse.model_validate(strategy), message="策略创建成功")


@router.get("", response_model=Response[List[StrategyResponse]])
async def list_strategies(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None, description="策略状态过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取策略列表"""
    strategies = await get_strategies(db, current_user.id, skip=skip, limit=limit, status=status)
    return Response(data=[StrategyResponse.model_validate(s) for s in strategies])


@router.get("/classic", response_model=Response[List[dict]])
async def classic_strategies(
    current_user: User = Depends(get_current_user),
):
    """获取经典策略列表（含详细描述、参数说明、适用场景）"""
    from app.services.strategy import get_classic_strategies
    strategies = await get_classic_strategies()
    return Response(data=strategies)


@router.post("/regression-test", response_model=Response[dict])
async def regression_test(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """对所有经典策略运行回归测试（使用标准模拟数据）"""
    from app.services.strategy import run_regression_test
    result = await run_regression_test(db)
    return Response(data=result, message="回归测试完成")


@router.get("/templates", response_model=Response[List[dict]])
async def strategy_templates(
    current_user: User = Depends(get_current_user),
):
    """获取内置策略模板列表"""
    templates = await get_strategy_templates()
    return Response(data=templates)


@router.get("/{strategy_id}", response_model=Response[StrategyResponse])
async def get_strategy(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取策略详情"""
    strategy = await get_strategy_by_id(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if strategy.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问该策略")
    return Response(data=StrategyResponse.model_validate(strategy))


@router.put("/{strategy_id}", response_model=Response[StrategyResponse])
async def update_strategy_endpoint(
    strategy_id: int,
    req: StrategyUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新策略"""
    strategy = await get_strategy_by_id(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if strategy.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权修改该策略")
    updated = await update_strategy(db, strategy_id, req.model_dump(exclude_unset=True))
    return Response(data=StrategyResponse.model_validate(updated), message="策略更新成功")


@router.delete("/{strategy_id}", response_model=Response)
async def delete_strategy_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除策略"""
    strategy = await get_strategy_by_id(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if strategy.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权删除该策略")
    await delete_strategy(db, strategy_id)
    return Response(message="策略已删除")


@router.post("/{strategy_id}/run", response_model=Response[dict])
async def run_strategy_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动运行策略"""
    strategy = await get_strategy_by_id(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if strategy.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权运行该策略")
    result = await run_strategy(db, strategy_id)
    return Response(data=result, message="策略执行完成")


@router.get("/{strategy_id}/logs", response_model=Response[List[StrategyRunLogResponse]])
async def strategy_logs(
    strategy_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取策略运行日志"""
    strategy = await get_strategy_by_id(db, strategy_id)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="策略不存在")
    if strategy.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权查看该策略日志")
    logs = await get_strategy_logs(db, strategy_id, skip=skip, limit=limit)
    return Response(data=[StrategyRunLogResponse.model_validate(log) for log in logs])
