"""
交易接口 - 订单管理、持仓查询、成交记录、持仓录入与导入"""
from __future__ import annotations
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.schemas.order import (
    OrderCreate, OrderResponse, TradeResponse, PositionResponse,
    PositionCreate, PositionImportResult,
)
from app.schemas.common import Response
from app.models.user import User
from app.models.order import OrderStatus
from app.services.trade import (
    create_order,
    cancel_order,
    get_orders,
    get_positions,
    get_trades,
    check_risk_before_trade,
    create_or_update_position,
    import_positions_from_excel,
    sync_positions_from_eastmoney,
    sync_orders_from_eastmoney,
)

router = APIRouter(prefix="/trade", tags=["实盘交易"])


@router.post("/orders", response_model=Response[OrderResponse])
async def create_order_endpoint(
    req: OrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建订单（支持限价单、市价单、止损单）"""
    # 风控前置检查
    risk_check = await check_risk_before_trade(db, current_user.id, req.symbol, req.model_dump())
    if not risk_check["passed"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=risk_check.get("message", "风控规则拒绝该交易"),
        )
    order = await create_order(db, current_user.id, req.model_dump())
    return Response(data=OrderResponse.model_validate(order), message="订单创建成功")


@router.post("/orders/{order_id}/cancel", response_model=Response[OrderResponse])
async def cancel_order_endpoint(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销订单"""
    order = await cancel_order(db, current_user.id, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="订单不存在或无法撤销")
    return Response(data=OrderResponse.model_validate(order), message="订单已撤销")


@router.get("/orders", response_model=Response[List[OrderResponse]])
async def list_orders(
    status: Optional[OrderStatus] = Query(None, description="按状态过滤"),
    symbol: Optional[str] = Query(None, description="按标的过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取订单列表"""
    orders = await get_orders(
        db, current_user.id, status=status, symbol=symbol, skip=skip, limit=limit
    )
    return Response(data=[OrderResponse.model_validate(o) for o in orders])


@router.get("/positions", response_model=Response[List[PositionResponse]])
async def list_positions(
    symbol: Optional[str] = Query(None, description="按标的过滤"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取持仓列表"""
    positions = await get_positions(db, current_user.id, symbol=symbol)
    return Response(data=[PositionResponse.model_validate(p) for p in positions])


@router.post("/positions", response_model=Response[PositionResponse])
async def create_position(
    req: PositionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """手动录入持仓（已存在则覆盖更新）"""
    position = await create_or_update_position(
        db, current_user.id, req.symbol, req.quantity, req.cost_price, req.leverage,
    )
    return Response(data=PositionResponse.model_validate(position), message="持仓已录入")


@router.post("/positions/import", response_model=Response[PositionImportResult])
async def import_positions(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """通过 Excel 文件批量导入持仓"""
    if not file.filename or not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="仅支持 .xlsx / .xls 格式的 Excel 文件",
        )
    # 10 MB limit — prevent zip bomb and memory exhaustion
    MAX_EXCEL_BYTES = 10 * 1024 * 1024
    try:
        content = await file.read()
        if len(content) > MAX_EXCEL_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"文件大小超过限制 ({MAX_EXCEL_BYTES // (1024*1024)} MB)",
            )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无法读取上传文件",
        )
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="文件为空")

    try:
        result = await import_positions_from_excel(db, current_user.id, content)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return Response(data=PositionImportResult(**result), message=f"导入完成: 新建 {result['created']}, 更新 {result['updated']}, 失败 {result['errors']}")


@router.post("/positions/sync", response_model=Response[dict])
async def sync_positions_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从东方财富账号同步持仓"""
    try:
        result = await sync_positions_from_eastmoney(db, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return Response(data=result, message=f"同步完成: 新建 {result['created']}, 更新 {result['updated']}, 共 {result['total']} 条")


@router.post("/orders/sync", response_model=Response[dict])
async def sync_orders_endpoint(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从东方财富同步今日委托状态"""
    try:
        result = await sync_orders_from_eastmoney(db, current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return Response(data=result, message=f"委托同步完成: 更新 {result['updated']} 条, 新增成交 {result['trades_added']} 条")


@router.get("/trades", response_model=Response[List[TradeResponse]])
async def list_trades(
    symbol: Optional[str] = Query(None, description="按标的过滤"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取成交记录"""
    trades = await get_trades(db, current_user.id, symbol=symbol, skip=skip, limit=limit)
    return Response(data=[TradeResponse.model_validate(t) for t in trades])
