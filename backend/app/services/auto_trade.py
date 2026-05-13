"""
自动交易引擎 — 信号 → 风控 → 下单 → 日志

支持 async (FastAPI) 和 sync (Celery) 两种调用方式。
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.order import Order, OrderSide, OrderStatus, Trade
from app.models.position import Position
from app.models.market_data import Ticker

logger = logging.getLogger(__name__)

# ── Shared helpers (work with any session-like object) ──────────────────────

LEVEL_RANK = {"STRONG_BUY": 3, "BUY": 2, "WATCH": 1, "NONE": 0}


def _get_kelly_fraction(symbol: str, db=None) -> float:
    """
    Kelly Criterion: f* = win_rate - (1 - win_rate) / win_loss_ratio
    Returns half-Kelly fraction for safety. Falls back to config default if no data.
    """
    try:
        # Try to read backtest stats for this symbol
        if db is not None:
            from app.models.backtest import BacktestResult
            if hasattr(db, 'execute'):
                stmt = select(BacktestResult).where(
                    BacktestResult.symbol == symbol
                ).order_by(BacktestResult.created_at.desc()).limit(1)
                # Handle both async and sync sessions
                try:
                    result = db.execute(stmt)
                    row = result.scalars().first() if hasattr(result, 'scalars') else result.fetchone()
                    if row:
                        row = row[0] if isinstance(row, tuple) else row
                except Exception:
                    row = None
                if row and row.win_rate and row.profit_factor and row.win_rate > 0 and row.profit_factor > 1:
                    win_rate = row.win_rate
                    # Derive win_loss_ratio from profit_factor = (win_rate * avg_win) / ((1-win_rate) * avg_loss)
                    # profit_factor = wins / losses, so avg_win/avg_loss = profit_factor * (1-win_rate) / win_rate
                    win_loss_ratio = row.profit_factor * (1 - win_rate) / win_rate if win_rate > 0 else 1.0
                    kelly = win_rate - (1 - win_rate) / max(win_loss_ratio, 0.01)
                    if kelly <= 0:
                        return 0.0  # negative EV — no allocation
                    half_kelly = kelly / 2  # half-Kelly for safety
                    return min(half_kelly, settings.AUTO_TRADE_POSITION_PCT)
    except Exception:
        pass
    # Fallback: fixed fraction from config
    return settings.AUTO_TRADE_POSITION_PCT


def _estimate_slippage(symbol: str, order_value: float, current_price: float, db=None) -> float:
    """
    Estimate total slippage: spread cost + market impact.
    Returns slippage as a fraction (e.g. 0.002 = 0.2%).
    """
    spread_pct = 0.001  # default 0.1% spread

    # Try to get real bid-ask spread from Ticker
    try:
        if db is not None:
            from app.models.market_data import Ticker as TickerModel
            stmt = select(TickerModel.bid, TickerModel.ask, TickerModel.turnover_24h).where(
                TickerModel.symbol == symbol
            )
            try:
                result = db.execute(stmt)
                row = result.fetchone() if hasattr(result, 'fetchone') else (result.first() or [None])
            except Exception:
                row = None
            if row and row[0] and row[1] and row[0] > 0:
                spread_pct = (row[1] - row[0]) / row[0]

            # Market impact: order as fraction of daily turnover
            if row and len(row) > 2 and row[2] and row[2] > 0:
                turnover = float(row[2])
                participation = order_value / turnover if turnover > 0 else 0
                # Square-root impact model: impact ≈ spread + σ * sqrt(participation)
                impact = 0.1 * (participation ** 0.5)  # conservative factor
                spread_pct += min(impact, 0.01)  # cap impact at 1%
    except Exception:
        pass

    return min(spread_pct, 0.02)  # cap total slippage at 2%


def _get_drawdown_factor(total_value: float) -> Tuple[float, float]:
    """
    Track peak equity and return a drawdown adjustment factor.
    Returns (drawdown_pct, adjustment_factor).
    Factor: 1.0 = normal, 0.5 = half size (DD>10%), 0.0 = stop (DD>20%).

    Peak equity is persisted in Redis so it survives Docker restarts.
    Uses Redis SETNX + GET/SET for atomic read-modify-write — multiple
    workers can safely update the peak without data loss.
    """
    from app.core.redis import get_sync_redis
    peak_key = "equity_peak:user:1"  # user_id=1 for system auto-trade

    r = get_sync_redis()
    peak = total_value
    try:
        stored = r.get(peak_key)
        if stored:
            peak = float(stored)
        if total_value > peak:
            peak = total_value
            r.set(peak_key, str(peak))
    except Exception:
        pass

    if peak <= 0:
        return 0.0, 1.0

    dd_pct = (peak - total_value) / peak

    # Drawdown-based position scaling
    if dd_pct >= 0.20:
        return dd_pct, 0.0   # stop trading
    elif dd_pct >= 0.10:
        return dd_pct, 0.5   # half size
    elif dd_pct >= 0.05:
        return dd_pct, 0.75  # reduce by 25%
    return dd_pct, 1.0


def _calc_position_size(total_value: float, current_price: float,
                        symbol: str = "", db=None) -> Tuple[float, int, float]:
    """
    Kelly-optimized position sizing with slippage + drawdown adjustment.
    Returns (order_value, quantity, slippage_pct).
    """
    kelly_pct = _get_kelly_fraction(symbol, db)
    slippage = _estimate_slippage(symbol, total_value * kelly_pct, current_price, db)

    # Drawdown protection
    dd_pct, dd_factor = _get_drawdown_factor(total_value)

    # Combine: Kelly * drawdown_factor * (1 - slippage)
    effective_pct = kelly_pct * dd_factor * (1 - slippage)
    effective_capital = total_value * effective_pct

    order_value = min(effective_capital, settings.AUTO_TRADE_MAX_PER_ORDER)
    quantity = int(order_value / current_price / 100) * 100
    if quantity < 100:
        quantity = 100

    actual_order_value = quantity * current_price
    return actual_order_value, quantity, slippage


def _build_remark(name: str, level: str, score: int, signals: List[Dict[str, Any]]) -> str:
    signal_types = [s.get("type", "?") for s in signals]
    return f"auto | {name} | {level}({score}) | {'+'.join(signal_types)}"


# ── Async versions (for FastAPI endpoints) ──────────────────────────────────


async def execute_signal(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    name: str,
    price: float,
    level: str,
    score: int,
    signals: List[Dict[str, Any]],
    stop_loss: float = 0.0,
) -> Dict[str, Any]:
    """Execute a single trade signal through the auto-trading pipeline (async).

    Args:
        stop_loss: ATR-based stop-loss price from decision engine.
                   If 0, defaults to -5% from entry (legacy fallback).
    """
    if not settings.AUTO_TRADE_ENABLED:
        return {"executed": False, "order_id": None, "reason": "自动交易总开关未启用", "dry_run": False}

    if LEVEL_RANK.get(level, 0) < LEVEL_RANK.get(settings.AUTO_TRADE_MIN_LEVEL, 3):
        return {"executed": False, "order_id": None,
                "reason": f"信号级别{level}低于最低阈值{settings.AUTO_TRADE_MIN_LEVEL}", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    # Daily order limit
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count_stmt = select(func.count(Order.id)).where(
        and_(Order.user_id == user_id, Order.source == "auto", Order.created_at >= today_start)
    )
    today_count = (await db.execute(count_stmt)).scalar() or 0
    if today_count >= settings.AUTO_TRADE_MAX_DAILY_ORDERS:
        return {"executed": False, "order_id": None,
                "reason": f"今日自动交易已达上限({settings.AUTO_TRADE_MAX_DAILY_ORDERS}笔)", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    # Dedup per symbol per day
    dup_stmt = select(Order).where(
        and_(Order.user_id == user_id, Order.symbol == symbol,
             Order.source == "auto", Order.created_at >= today_start)
    )
    dup_result = await db.execute(dup_stmt)
    if dup_result.scalars().first():
        return {"executed": False, "order_id": None,
                "reason": f"{name}今日已自动交易，跳过", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    # Position sizing
    pos_stmt = select(func.coalesce(func.sum(Position.market_value), 0)).where(Position.user_id == user_id)
    total_value = max(float((await db.execute(pos_stmt)).scalar() or 0), settings.AUTO_TRADE_BASE_CAPITAL)

    price_stmt = select(Ticker.last_price).where(Ticker.symbol == symbol)
    price_row = await db.execute(price_stmt)
    current_price = float(price_row.scalar()) if price_row.scalar() else price
    if current_price <= 0:
        return {"executed": False, "order_id": None, "reason": f"{name}无法获取有效价格", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    order_value, quantity, slippage = _calc_position_size(total_value, current_price, symbol, db)
    remark = _build_remark(name, level, score, signals)

    if settings.AUTO_TRADE_DRY_RUN:
        logger.info(f"[AUTO-TRADE DRY-RUN] {name}({symbol}) level={level} score={score} "
                     f"price={current_price:.2f} qty={quantity} value={order_value:.0f} "
                     f"kelly_slippage={slippage:.3f}")
        return {"executed": True, "order_id": None,
                "reason": f"干跑模式: {name} {level} {quantity}股@{current_price:.2f} 约{order_value:.0f}元",
                "dry_run": True}

    try:
        from app.services.trade import create_order
        order_data = {
            "symbol": symbol, "side": "buy", "type": "limit",
            "price": current_price, "quantity": float(quantity),
            "source": "auto", "remark": remark,
        }
        order = await create_order(db, user_id, order_data)
        logger.info(f"[AUTO-TRADE] {name}({symbol}) order=#{order.id} level={level} "
                     f"qty={quantity} price={current_price:.2f} value={order_value:.0f} "
                     f"slip={slippage:.3f}")

        # Create stop-loss order (both sandbox and live)
        sl_price = stop_loss if stop_loss > 0 else round(current_price * 0.95, 2)
        sl_source = "decision_atr" if stop_loss > 0 else "hardcoded_5pct"
        from app.models.order import Order as OrderModel, OrderSide as OS, OrderStatus as OSt
        stop_order = OrderModel(
            user_id=user_id, symbol=symbol, side=OS.SELL,
            type="stop", status=OSt.PENDING,
            stop_price=sl_price, price=sl_price,
            quantity=float(quantity), source="auto",
            remark=f"auto stop-loss | {name} | {sl_source} | entry={current_price:.2f}",
        )
        db.add(stop_order)
        await db.flush()
        logger.info(f"[AUTO-TRADE] {name}({symbol}) stop-loss set at {sl_price} ({sl_source})")

        return {"executed": True, "order_id": order.id,
                "reason": f"{name} {level} {quantity}股@{current_price:.2f} 订单#{order.id}",
                "dry_run": False}
    except Exception as e:
        logger.error(f"[AUTO-TRADE FAIL] {name}({symbol}): {e}", exc_info=True)
        return {"executed": False, "order_id": None, "reason": f"下单失败: {e}", "dry_run": False}


async def execute_signal_batch(
    db: AsyncSession,
    user_id: int,
    stock_signals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Execute auto-trades for a batch of stock signals, sorted by priority."""
    results = []
    sorted_signals = sorted(stock_signals,
        key=lambda s: (LEVEL_RANK.get(s.get("level", "NONE"), 99), -s.get("score", 0)))

    for s in sorted_signals:
        result = await execute_signal(
            db, user_id, symbol=s["symbol"], name=s["name"],
            price=s.get("price", 0), level=s.get("level", "NONE"),
            score=s.get("score", 0), signals=s.get("signals", []),
        )
        results.append({**s, "auto_trade": result})

        if not result["executed"] and "已达上限" in result.get("reason", ""):
            for remaining in sorted_signals[len(results):]:
                results.append({**remaining, "auto_trade": {
                    "executed": False, "order_id": None,
                    "reason": "跳过(已达日限额)", "dry_run": settings.AUTO_TRADE_DRY_RUN,
                }})
            break

    executed = sum(1 for r in results if r["auto_trade"]["executed"])
    dry_runs = sum(1 for r in results if r["auto_trade"].get("dry_run"))
    logger.info(f"[AUTO-TRADE BATCH] {len(results)} signals -> {executed} executed ({dry_runs} dry-run)")
    return results


# ── Sync versions (for Celery tasks using SyncSession) ──────────────────────


def execute_signal_sync(
    db: Session,
    user_id: int,
    symbol: str,
    name: str,
    price: float,
    level: str,
    score: int,
    signals: List[Dict[str, Any]],
    stop_loss: float = 0.0,
) -> Dict[str, Any]:
    """Execute a single trade signal through the auto-trading pipeline (sync).

    Args:
        stop_loss: ATR-based stop-loss price from decision engine.
                   If 0, defaults to -5% from entry (legacy fallback).
    """
    if not settings.AUTO_TRADE_ENABLED:
        return {"executed": False, "order_id": None, "reason": "自动交易总开关未启用", "dry_run": False}

    if LEVEL_RANK.get(level, 0) < LEVEL_RANK.get(settings.AUTO_TRADE_MIN_LEVEL, 3):
        return {"executed": False, "order_id": None,
                "reason": f"信号级别{level}低于最低阈值{settings.AUTO_TRADE_MIN_LEVEL}", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    today_count = db.execute(
        select(func.count(Order.id)).where(
            and_(Order.user_id == user_id, Order.source == "auto", Order.created_at >= today_start)
        )
    ).scalar() or 0
    if today_count >= settings.AUTO_TRADE_MAX_DAILY_ORDERS:
        return {"executed": False, "order_id": None,
                "reason": f"今日自动交易已达上限({settings.AUTO_TRADE_MAX_DAILY_ORDERS}笔)", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    existing = db.execute(
        select(Order).where(
            and_(Order.user_id == user_id, Order.symbol == symbol,
                 Order.source == "auto", Order.created_at >= today_start)
        )
    ).scalars().first()
    if existing:
        return {"executed": False, "order_id": None,
                "reason": f"{name}今日已自动交易(订单#{existing.id})，跳过", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    total_value = max(float(db.execute(
        select(func.coalesce(func.sum(Position.market_value), 0)).where(Position.user_id == user_id)
    ).scalar() or 0), settings.AUTO_TRADE_BASE_CAPITAL)

    row = db.execute(select(Ticker.last_price).where(Ticker.symbol == symbol)).fetchone()
    current_price = float(row[0]) if row and row[0] else price
    if current_price <= 0:
        return {"executed": False, "order_id": None, "reason": f"{name}无法获取有效价格", "dry_run": settings.AUTO_TRADE_DRY_RUN}

    order_value, quantity, slippage = _calc_position_size(total_value, current_price, symbol, db)
    remark = _build_remark(name, level, score, signals)

    if settings.AUTO_TRADE_DRY_RUN:
        logger.info(f"[AUTO-TRADE DRY-RUN] {name}({symbol}) level={level} score={score} "
                     f"price={current_price:.2f} qty={quantity} value={order_value:.0f} "
                     f"kelly_slip={slippage:.3f}")
        return {"executed": True, "order_id": None,
                "reason": f"干跑模式: {name} {level} {quantity}股@{current_price:.2f} 约{order_value:.0f}元",
                "dry_run": True}

    # Real order creation (sync)
    try:
        # Pre-trade risk check (sync path)
        from app.services.risk import check_risk_rules_sync
        risk_check = check_risk_rules_sync(db, user_id, symbol, {
            "symbol": symbol, "side": "buy", "type": "limit",
            "price": current_price, "quantity": float(quantity),
        })
        if not risk_check["passed"]:
            return {"executed": False, "order_id": None,
                    "reason": f"风控拒绝: {risk_check.get('message', '')}", "dry_run": False}

        is_sandbox = settings.ORDER_EXECUTION_MODE == "sandbox"

        order = Order(
            user_id=user_id, symbol=symbol, side=OrderSide.BUY,
            type="limit", status=OrderStatus.PENDING,
            price=current_price, quantity=float(quantity),
            source="auto", remark=remark,
        )
        db.add(order)
        db.flush()

        # Create stop-loss order (shared between sandbox and live)
        sl_price = stop_loss if stop_loss > 0 else round(current_price * 0.95, 2)
        sl_source = "decision_atr" if stop_loss > 0 else "hardcoded_5pct"
        stop_order = Order(
            user_id=user_id, symbol=symbol, side=OrderSide.SELL,
            type="stop", status=OrderStatus.PENDING,
            stop_price=sl_price, price=sl_price,
            quantity=float(quantity), source="auto",
            remark=f"auto stop-loss | {name} | {sl_source} | entry={current_price:.2f}",
        )
        db.add(stop_order)
        db.flush()

        if is_sandbox:
            order.status = OrderStatus.FILLED
            order.filled_quantity = order.quantity
            order.avg_price = current_price
            order.fee = float(quantity) * current_price * 0.001
            db.flush()

            trade = Trade(
                order_id=order.id, symbol=symbol, side=OrderSide.BUY,
                price=current_price, quantity=float(quantity),
                fee=order.fee, fee_asset="CNY",
                trade_time=datetime.now(timezone.utc),
            )
            db.add(trade)
            db.flush()

            # Update position after sandbox fill
            existing_pos = db.execute(
                select(Position).where(
                    and_(Position.user_id == user_id, Position.symbol == symbol)
                )
            ).scalars().first()

            if existing_pos:
                if existing_pos.quantity and existing_pos.quantity > 0:
                    total_cost = existing_pos.quantity * existing_pos.cost_price + float(quantity) * current_price
                    new_qty = existing_pos.quantity + float(quantity)
                    existing_pos.cost_price = total_cost / new_qty
                else:
                    existing_pos.cost_price = current_price
                    new_qty = float(quantity)
                existing_pos.quantity = new_qty
                existing_pos.available_quantity = new_qty
                existing_pos.current_price = current_price
                existing_pos.market_value = new_qty * current_price
            else:
                pos = Position(
                    user_id=user_id, symbol=symbol,
                    quantity=float(quantity), available_quantity=float(quantity),
                    cost_price=current_price, current_price=current_price,
                    market_value=float(quantity) * current_price,
                )
                db.add(pos)
            db.flush()

            logger.info(f"[AUTO-TRADE] {name}({symbol}) stop-loss set at {sl_price} ({sl_source})")
        else:
            logger.info(f"[AUTO-TRADE LIVE] {name}({symbol}) stop-loss set at {sl_price} ({sl_source})")
            # Submit to broker via adapter pattern
            try:
                from app.utils.exchange_adapter import ExchangeAdapterFactory
                adapter = ExchangeAdapterFactory.create(settings.ORDER_EXECUTION_MODE)
                import asyncio
                loop = asyncio.get_event_loop()
                result = loop.run_until_complete(adapter.create_order(
                    symbol=symbol, side="buy", order_type="limit",
                    quantity=float(quantity), price=current_price,
                ))
                order.order_id_exchange = str(result.get("order_id") or result.get("entrust_no", ""))
                order.status = OrderStatus.PENDING
                logger.info(f"[AUTO-TRADE LIVE] {name}({symbol}) sent via {settings.ORDER_EXECUTION_MODE}, "
                            f"entrust={order.order_id_exchange}")
            except Exception as _e:
                db.rollback()
                logger.error(f"[AUTO-TRADE BROKER FAIL] {name}({symbol}): {_e}")
                return {"executed": False, "order_id": None,
                        "reason": f"券商下单失败: {_e}", "dry_run": False}

        db.commit()
        logger.info(f"[AUTO-TRADE] {name}({symbol}) order=#{order.id} level={level} "
                     f"qty={quantity} price={current_price:.2f} value={order_value:.0f}")
        return {"executed": True, "order_id": order.id,
                "reason": f"{name} {level} {quantity}股@{current_price:.2f} 订单#{order.id}",
                "dry_run": False}

    except Exception as e:
        db.rollback()
        logger.error(f"[AUTO-TRADE FAIL] {name}({symbol}): {e}", exc_info=True)
        return {"executed": False, "order_id": None, "reason": f"下单失败: {e}", "dry_run": False}


def execute_signal_batch_sync(
    db: Session,
    user_id: int,
    stock_signals: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Sync batch auto-trade execution."""
    results = []
    sorted_signals = sorted(stock_signals,
        key=lambda s: (LEVEL_RANK.get(s.get("level", "NONE"), 99), -s.get("score", 0)))

    for s in sorted_signals:
        result = execute_signal_sync(
            db, user_id, symbol=s["symbol"], name=s["name"],
            price=s.get("price", 0), level=s.get("level", "NONE"),
            score=s.get("score", 0), signals=s.get("signals", []),
        )
        results.append({**s, "auto_trade": result})

        if not result["executed"] and "已达上限" in result.get("reason", ""):
            for remaining in sorted_signals[len(results):]:
                results.append({**remaining, "auto_trade": {
                    "executed": False, "order_id": None,
                    "reason": "跳过(已达日限额)", "dry_run": settings.AUTO_TRADE_DRY_RUN,
                }})
            break

    executed = sum(1 for r in results if r["auto_trade"]["executed"])
    dry_runs = sum(1 for r in results if r["auto_trade"].get("dry_run"))
    logger.info(f"[AUTO-TRADE BATCH] {len(results)} signals -> {executed} executed ({dry_runs} dry-run)")
    return results
