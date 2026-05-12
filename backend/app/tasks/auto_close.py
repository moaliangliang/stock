"""
自动平仓任务 — 止损检查 + 超期持仓自动卖出
"""
from datetime import datetime, timezone, timedelta

from loguru import logger
from sqlalchemy import select, and_

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.core.redis import TaskLock
from app.models.order import Order, OrderSide, OrderStatus, Trade
from app.models.position import Position
from app.models.market_data import Ticker


@celery_app.task(queue="market")
def check_stop_orders():
    """检查待触发止损单，当前价触及止损价则自动卖出。"""
    with TaskLock("check_stop_orders", timeout=60) as acquired:
        if not acquired:
            return {"skipped": "another instance is running"}
        db = SyncSessionLocal()
        try:
            stop_orders = db.execute(
                select(Order).where(
                    and_(
                        Order.type == "stop",
                        Order.status == OrderStatus.PENDING,
                        Order.source == "auto",
                    )
                )
            ).scalars().all()

            if not stop_orders:
                db.close()
                return {"checked": 0, "triggered": 0}

            triggered = 0
            for order in stop_orders:
                ticker = db.execute(
                    select(Ticker.last_price).where(Ticker.symbol == order.symbol)
                ).fetchone()

                if not ticker or not ticker[0]:
                    continue

                current_price = float(ticker[0])
                if current_price <= 0:
                    continue

                if order.side == OrderSide.SELL and current_price <= (order.stop_price or 99999):
                    order.status = OrderStatus.FILLED
                    order.filled_quantity = order.quantity
                    order.avg_price = current_price
                    order.fee = float(order.quantity) * current_price * 0.001

                    trade = Trade(
                        order_id=order.id, symbol=order.symbol, side=OrderSide.SELL,
                        price=current_price, quantity=order.quantity,
                        fee=order.fee, fee_asset="CNY",
                        trade_time=datetime.now(timezone.utc),
                    )
                    db.add(trade)

                    pos = db.execute(
                        select(Position).where(
                            and_(Position.user_id == order.user_id, Position.symbol == order.symbol)
                        )
                    ).scalars().first()

                    if pos:
                        sold_qty = float(order.quantity)
                        pos.available_quantity = max(0, (pos.available_quantity or 0) - sold_qty)
                        pos.quantity = max(0, (pos.quantity or 0) - sold_qty)
                        if pos.quantity <= 0:
                            db.delete(pos)

                    triggered += 1
                    logger.info(
                        f"[STOP-LOSS] 触发: {order.symbol} "
                        f"当前价={current_price:.2f} 止损价={order.stop_price} "
                        f"卖出{int(order.quantity)}股"
                    )

            if triggered > 0:
                db.commit()
                logger.info(f"[STOP-LOSS] 共触发 {triggered} 笔止损")
            else:
                db.rollback()

            db.close()
            return {"checked": len(stop_orders), "triggered": triggered}
        except Exception as e:
            db.rollback()
            logger.error(f"[STOP-LOSS] 检查失败: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            try:
                db.close()
            except Exception:
                pass


@celery_app.task(queue="market")
def close_expired_positions():
    """自动交易持仓超过 5 个交易日（7 个自然日）则自动市价卖出。"""
    with TaskLock("close_expired_positions", timeout=300) as acquired:
        if not acquired:
            return {"skipped": "another instance is running"}
        db = SyncSessionLocal()
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)

            old_buys = db.execute(
                select(Order).where(
                    and_(
                        Order.source == "auto",
                        Order.side == OrderSide.BUY,
                        Order.status == OrderStatus.FILLED,
                        Order.created_at < cutoff,
                    )
                ).order_by(Order.created_at.asc())
            ).scalars().all()

            if not old_buys:
                db.close()
                return {"checked": 0, "closed": 0}

            closed = 0
            for buy_order in old_buys:
                pos = db.execute(
                    select(Position).where(
                        and_(
                            Position.user_id == buy_order.user_id,
                            Position.symbol == buy_order.symbol,
                            Position.quantity > 0,
                        )
                    )
                ).scalars().first()

                if not pos:
                    continue

                ticker = db.execute(
                    select(Ticker.last_price).where(Ticker.symbol == buy_order.symbol)
                ).fetchone()
                current_price = float(ticker[0]) if ticker and ticker[0] else 0
                if current_price <= 0:
                    continue

                qty = int(pos.available_quantity or pos.quantity)
                if qty <= 0:
                    continue

                sell_order = Order(
                    user_id=buy_order.user_id,
                    symbol=buy_order.symbol,
                    side=OrderSide.SELL,
                    type="market",
                    status=OrderStatus.FILLED,
                    price=current_price,
                    quantity=float(qty),
                    filled_quantity=float(qty),
                    avg_price=current_price,
                    fee=float(qty) * current_price * 0.001,
                    source="auto",
                    remark=f"auto close | 持仓超5交易日 | 入场价={buy_order.avg_price}",
                )
                db.add(sell_order)
                db.flush()

                trade = Trade(
                    order_id=sell_order.id,
                    symbol=sell_order.symbol,
                    side=OrderSide.SELL,
                    price=current_price,
                    quantity=float(qty),
                    fee=sell_order.fee,
                    fee_asset="CNY",
                    trade_time=datetime.now(timezone.utc),
                )
                db.add(trade)

                pos.available_quantity = max(0, (pos.available_quantity or 0) - qty)
                pos.quantity = max(0, (pos.quantity or 0) - qty)
                if pos.quantity <= 0:
                    db.delete(pos)

                pnl_str = ""
                if buy_order.avg_price and buy_order.avg_price > 0:
                    pnl_pct = (current_price - buy_order.avg_price) / buy_order.avg_price * 100
                    pnl_str = f" 盈亏={pnl_pct:+.2f}%"

                closed += 1
                logger.info(
                    f"[AUTO-CLOSE] {buy_order.symbol} 超5日平仓 "
                    f"入场={buy_order.avg_price} 出场={current_price}{pnl_str}"
                )

            if closed > 0:
                db.commit()
                logger.info(f"[AUTO-CLOSE] 共平仓 {closed} 笔超期持仓")
            else:
                db.rollback()

            db.close()
            return {"checked": len(old_buys), "closed": closed}
        except Exception as e:
            db.rollback()
            logger.error(f"[AUTO-CLOSE] 失败: {e}", exc_info=True)
            return {"error": str(e)}
        finally:
            try:
                db.close()
            except Exception:
                pass
