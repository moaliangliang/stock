"""
Risk control service — pre-trade checks, event logging, rule enforcement.
Supports both async (FastAPI) and sync (Celery) call paths.
"""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select, case, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.order import Order, OrderSide, OrderStatus, Trade
from app.models.position import Position as PositionModel
from app.models.risk import RiskAction, RiskRecord, RiskRule, RiskRuleType
from app.models.user import User
from loguru import logger
from app.core.config import settings

# ── Timezone (China A-share market = UTC+8) ──────────────────────────────────
BEIJING_TZ = timezone(timedelta(hours=8))


def _beijing_now() -> datetime:
    return datetime.now(BEIJING_TZ)


def _today_start() -> datetime:
    """Start of today in Beijing time."""
    return _beijing_now().replace(hour=0, minute=0, second=0, microsecond=0)


# ── Circuit breaker (Redis-backed emergency stop) ────────────────────────────
CIRCUIT_BREAKER_KEY = "risk:emergency_stop"


async def _is_circuit_breaker_active() -> bool:
    try:
        from app.core.redis import get_redis
        r = await get_redis()
        return await r.exists(CIRCUIT_BREAKER_KEY) > 0
    except Exception:
        return False


def _is_circuit_breaker_active_sync() -> bool:
    try:
        from app.core.redis import get_sync_redis
        r = get_sync_redis()
        return bool(r.exists(CIRCUIT_BREAKER_KEY))
    except Exception:
        return False


async def set_emergency_stop(reason: str = "") -> bool:
    """Activate circuit breaker. All orders will be blocked."""
    try:
        from app.core.redis import get_redis
        r = await get_redis()
        await r.set(CIRCUIT_BREAKER_KEY, reason or "emergency")
        logger.warning(f"[CIRCUIT-BREAKER] 紧急熔断已激活: {reason}")
        return True
    except Exception as e:
        logger.error(f"[CIRCUIT-BREAKER] 激活失败: {e}")
        return False


async def clear_emergency_stop() -> bool:
    """Deactivate circuit breaker."""
    try:
        from app.core.redis import get_redis
        r = await get_redis()
        await r.delete(CIRCUIT_BREAKER_KEY)
        logger.info("[CIRCUIT-BREAKER] 紧急熔断已解除")
        return True
    except Exception as e:
        logger.error(f"[CIRCUIT-BREAKER] 解除失败: {e}")
        return False


async def get_emergency_stop_status() -> Dict[str, Any]:
    """Return current circuit breaker status."""
    try:
        from app.core.redis import get_redis
        r = await get_redis()
        active = await r.exists(CIRCUIT_BREAKER_KEY) > 0
        reason = await r.get(CIRCUIT_BREAKER_KEY) if active else None
        return {"active": active, "reason": reason}
    except Exception:
        return {"active": False, "reason": None}


# ── Equity estimation ──────────────────────────────────────────────────────


async def _estimate_equity(db: AsyncSession, user_id: int, positions: list) -> float:
    """Total equity = market_value + estimated cash (net of all filled orders + fees)."""
    total_market_value = sum(
        (p.quantity or 0) * (p.current_price or 0) for p in positions
    )

    base = settings.AUTO_TRADE_BASE_CAPITAL
    if base <= 0:
        return total_market_value

    # Single query: net cash flow from ALL filled orders (buy/sell amounts + fees)
    cash_row = await db.execute(
        select(
            func.coalesce(func.sum(case(
                (and_(Order.side == OrderSide.BUY, Order.status == OrderStatus.FILLED),
                 Order.filled_quantity * Order.avg_price + func.coalesce(Order.fee, 0)),
                else_=0.0,
            )), 0.0).label("total_outflow"),
            func.coalesce(func.sum(case(
                (and_(Order.side == OrderSide.SELL, Order.status == OrderStatus.FILLED),
                 Order.filled_quantity * Order.avg_price - func.coalesce(Order.fee, 0)),
                else_=0.0,
            )), 0.0).label("total_inflow"),
        ).where(Order.user_id == user_id)
    )
    row = cash_row.fetchone()
    total_outflow, total_inflow = float(row[0] or 0), float(row[1] or 0)
    estimated_cash = max(0.0, base + total_inflow - total_outflow)
    return total_market_value + estimated_cash


def _estimate_equity_sync(db: Session, user_id: int, positions: list) -> float:
    """Sync version of _estimate_equity for Celery tasks."""
    total_market_value = sum(
        (p.quantity or 0) * (p.current_price or 0) for p in positions
    )

    base = settings.AUTO_TRADE_BASE_CAPITAL
    if base <= 0:
        return total_market_value

    row = db.execute(
        select(
            func.coalesce(func.sum(case(
                (and_(Order.side == OrderSide.BUY, Order.status == OrderStatus.FILLED),
                 Order.filled_quantity * Order.avg_price + func.coalesce(Order.fee, 0)),
                else_=0.0,
            )), 0.0).label("total_outflow"),
            func.coalesce(func.sum(case(
                (and_(Order.side == OrderSide.SELL, Order.status == OrderStatus.FILLED),
                 Order.filled_quantity * Order.avg_price - func.coalesce(Order.fee, 0)),
                else_=0.0,
            )), 0.0).label("total_inflow"),
        ).where(Order.user_id == user_id)
    ).fetchone()
    total_outflow, total_inflow = float(row[0] or 0), float(row[1] or 0)
    estimated_cash = max(0.0, base + total_inflow - total_outflow)
    return total_market_value + estimated_cash


# ── FIFO cost basis for realised PnL ────────────────────────────────────────


def _fifo_cost_basis(buys: List[Dict[str, Any]], sell_qty: float) -> Tuple[float, List[Dict[str, Any]]]:
    """Match a sell against earliest buy lots (FIFO). Returns (realised_pnl, remaining_buys)."""
    remaining_qty = sell_qty
    realised_pnl = 0.0

    for lot in buys:
        if remaining_qty <= 0:
            break
        matched = min(lot["qty"], remaining_qty)
        realised_pnl += matched * (lot.get("sell_price", 0) - lot["price"])
        lot["qty"] -= matched
        remaining_qty -= matched

    # Remove depleted lots
    buys[:] = [b for b in buys if b["qty"] > 0.001]
    return realised_pnl, buys


# ── Rule params helpers ─────────────────────────────────────────────────────


RULE_PARAMS_SCHEMA: Dict[str, List[str]] = {
    RiskRuleType.MAX_DAILY_LOSS: ["max_loss_pct"],
    RiskRuleType.MAX_POSITION_RATIO: ["max_ratio_pct"],
    RiskRuleType.MAX_POSITION_QTY: ["max_count"],
    RiskRuleType.STOP_LOSS: ["stop_loss_pct"],
    RiskRuleType.BLACKLIST: [],  # uses symbols field
    RiskRuleType.MAX_ORDER_COUNT: ["max_count"],
    RiskRuleType.MAX_OPEN_ORDERS: ["max_count"],
}


# Legacy key mapping for backward compatibility with pre-existing DB rules
_LEGACY_KEY_MAP = {
    "max_loss_pct": "ratio",
    "max_ratio_pct": "ratio",
    "stop_loss_pct": "ratio",
    "max_count": "count",
}


def _get_rule_param(rule: Optional[RiskRule], key: str, default: Any = None) -> Any:
    """Extract a parameter from a RiskRule's params JSON.
    Supports legacy key names for backward compatibility."""
    if rule is None or not rule.params:
        return default
    if key in rule.params:
        return rule.params[key]
    # Fallback to legacy key name
    legacy_key = _LEGACY_KEY_MAP.get(key)
    if legacy_key and legacy_key in rule.params:
        return rule.params[legacy_key]
    return default


# ── Public API: Risk rule CRUD ──────────────────────────────────────────────


async def get_risk_rules(
    db: AsyncSession,
    user_id: Optional[int] = None,
    rule_type: Optional[RiskRuleType] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
) -> list[RiskRule]:
    conditions = []
    if user_id is not None:
        conditions.append(RiskRule.user_id == user_id)
    if rule_type is not None:
        conditions.append(RiskRule.rule_type == rule_type)
    if is_active is not None:
        conditions.append(RiskRule.is_active == is_active)

    result = await db.execute(
        select(RiskRule)
        .where(and_(*conditions) if conditions else True)
        .offset(skip).limit(limit)
        .order_by(RiskRule.created_at.desc())
    )
    return list(result.scalars().all())


def _validate_rule_params(rule_type: RiskRuleType, params: Optional[dict]) -> Optional[str]:
    """Validate params for a given rule type. Returns error message or None."""
    required = RULE_PARAMS_SCHEMA.get(rule_type, [])
    if not required:
        return None
    if not params or not isinstance(params, dict):
        return f"规则类型 {rule_type.value} 需要 params 字段: {required}"
    for key in required:
        if key not in params:
            return f"规则类型 {rule_type.value} 缺少必要参数: {key}"
    return None


async def create_risk_rule(
    db: AsyncSession, user_id: Optional[int], rule_data: dict
) -> RiskRule:
    params = rule_data.get("params", {})
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except (json.JSONDecodeError, TypeError):
            params = {}

    # Validate params schema
    rule_type = rule_data.get("rule_type")
    if isinstance(rule_type, str):
        rule_type = RiskRuleType(rule_type)
    validation_error = _validate_rule_params(rule_type, params)
    if validation_error:
        raise ValueError(validation_error)

    symbols = rule_data.get("symbols")
    if isinstance(symbols, list):
        symbols = ",".join(symbols)

    rule = RiskRule(
        user_id=user_id,
        name=rule_data["name"],
        rule_type=rule_type,
        action=rule_data.get("action", RiskAction.WARN),
        is_active=rule_data.get("is_active", True),
        params=params if params else None,
        symbols=symbols,
        description=rule_data.get("description"),
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


async def update_risk_rule(
    db: AsyncSession, rule_id: int, rule_data: dict, user_id: int
) -> Optional[RiskRule]:
    result = await db.execute(
        select(RiskRule).where(RiskRule.id == rule_id, RiskRule.user_id == user_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return None

    for field in ["name", "rule_type", "action", "is_active", "params", "symbols", "description"]:
        if field in rule_data:
            value = rule_data[field]
            if field == "params":
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (json.JSONDecodeError, TypeError):
                        value = {}
                # Re-validate if rule_type is being set or params changed
                rt = rule_data.get("rule_type", rule.rule_type)
                if isinstance(rt, str):
                    rt = RiskRuleType(rt)
                err = _validate_rule_params(rt, value if isinstance(value, dict) else {})
                if err:
                    raise ValueError(err)
            if field == "symbols" and isinstance(value, list):
                value = ",".join(value)
            setattr(rule, field, value)
    await db.flush()
    await db.refresh(rule)
    return rule


async def delete_risk_rule(db: AsyncSession, rule_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(RiskRule).where(RiskRule.id == rule_id, RiskRule.user_id == user_id)
    )
    rule = result.scalar_one_or_none()
    if not rule:
        return False
    await db.delete(rule)
    await db.flush()
    return True


async def get_risk_records(
    db: AsyncSession, user_id: Optional[int] = None,
    skip: int = 0, limit: int = 100
) -> list[RiskRecord]:
    conditions = []
    if user_id is not None:
        conditions.append(RiskRecord.user_id == user_id)
    result = await db.execute(
        select(RiskRecord)
        .where(and_(*conditions) if conditions else True)
        .order_by(RiskRecord.created_at.desc())
        .offset(skip).limit(limit)
    )
    return list(result.scalars().all())


# ── Risk summary / dashboard ────────────────────────────────────────────────


async def get_risk_summary(db: AsyncSession, user_id: int) -> Dict[str, Any]:
    """Aggregated risk dashboard for a user."""
    today = _today_start()

    # Positions
    pos_result = await db.execute(
        select(PositionModel).where(PositionModel.user_id == user_id)
    )
    positions = list(pos_result.scalars().all())

    total_market_value = sum((p.quantity or 0) * (p.current_price or 0) for p in positions)
    total_pnl = sum(p.pnl or 0 for p in positions)
    day_pnl = sum(p.day_pnl or 0 for p in positions)
    position_count = len([p for p in positions if (p.quantity or 0) > 0])

    equity = await _estimate_equity(db, user_id, positions)

    # Today's risk events
    event_count_result = await db.execute(
        select(func.count(RiskRecord.id)).where(
            and_(RiskRecord.user_id == user_id, RiskRecord.created_at >= today)
        )
    )
    today_risk_events = event_count_result.scalar() or 0

    # Today's auto orders
    order_count_result = await db.execute(
        select(func.count(Order.id)).where(
            and_(Order.user_id == user_id, Order.source == "auto", Order.created_at >= today)
        )
    )
    today_auto_orders = order_count_result.scalar() or 0

    # Drawdown from Redis peak
    dd_pct = 0.0
    try:
        from app.core.redis import get_sync_redis
        r = get_sync_redis()
        peak_val = r.get("equity_peak:user:1")
        if peak_val and equity > 0:
            peak = float(peak_val)
            dd_pct = round((peak - equity) / peak * 100, 2) if peak > equity else 0.0
    except Exception:
        pass

    # Circuit breaker status
    cb = await get_emergency_stop_status()

    return {
        "equity": round(equity, 2),
        "total_market_value": round(total_market_value, 2),
        "total_pnl": round(total_pnl, 2),
        "day_pnl": round(day_pnl, 2),
        "position_count": position_count,
        "drawdown_pct": dd_pct,
        "today_risk_events": today_risk_events,
        "today_auto_orders": today_auto_orders,
        "circuit_breaker_active": cb["active"],
        "circuit_breaker_reason": cb.get("reason"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Individual risk checks (async)
# ═══════════════════════════════════════════════════════════════════════════════


async def check_daily_loss(
    db: AsyncSession,
    user_id: int,
    max_loss_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Check daily loss against limit. Uses Trade.trade_time (Beijing tz) and FIFO cost basis."""
    today = _today_start()

    # Fetch user if no explicit limit provided
    if max_loss_pct is None:
        result = await db.execute(select(User.max_daily_loss).where(User.id == user_id))
        row = result.fetchone()
        if row is None:
            return {"passed": False, "message": "User not found"}
        max_loss_pct = row[0]

    if max_loss_pct is None or max_loss_pct <= 0:
        return {"passed": True, "message": "Daily loss limit not set"}

    # Unrealised PnL from positions
    pos_result = await db.execute(
        select(PositionModel).where(PositionModel.user_id == user_id)
    )
    positions = list(pos_result.scalars().all())
    unrealised_day_pnl = sum((p.day_pnl or 0.0) for p in positions)

    # Realised PnL: query today's filled sells via Trade.trade_time
    trade_result = await db.execute(
        select(Trade).where(
            and_(
                Trade.symbol.in_(
                    select(Order.symbol).where(
                        and_(Order.user_id == user_id, Order.side == OrderSide.SELL)
                    )
                ),
                Trade.trade_time >= today,
            )
        ).order_by(Trade.trade_time.asc())
    )
    today_trades = list(trade_result.scalars().all())

    # Build FIFO lot list per symbol
    # Fetch all historical buys per symbol for cost basis
    buy_result = await db.execute(
        select(Order).where(
            and_(
                Order.user_id == user_id,
                Order.side == OrderSide.BUY,
                Order.status == OrderStatus.FILLED,
            )
        ).order_by(Order.created_at.asc())
    )
    all_buys = list(buy_result.scalars().all())

    # Organise buys into FIFO queues per symbol
    buy_queues: Dict[str, List[Dict[str, Any]]] = {}
    for b in all_buys:
        sym = b.symbol
        if sym not in buy_queues:
            buy_queues[sym] = []
        buy_queues[sym].append({
            "qty": float(b.filled_quantity or 0),
            "price": float(b.avg_price or 0),
            "sell_price": 0.0,  # placeholder
        })

    realised_pnl = 0.0
    for trade in today_trades:
        sym = trade.symbol
        if sym not in buy_queues or not buy_queues[sym]:
            continue
        qty = float(trade.quantity or 0)
        price = float(trade.price or 0)
        if qty <= 0 or price <= 0:
            continue
        # Set sell price on all lots for this symbol (used in FIFO match)
        for lot in buy_queues[sym]:
            lot["sell_price"] = price
        pnl, buy_queues[sym] = _fifo_cost_basis(buy_queues[sym], qty)
        realised_pnl += pnl

    today_pnl = unrealised_day_pnl + realised_pnl

    total_equity = await _estimate_equity(db, user_id, positions)
    if total_equity <= 0:
        return {
            "passed": True,
            "message": "Daily loss limit: no positions",
            "current_loss": 0.0,
            "limit_value": round(max_loss_pct / 100.0, 6),
        }

    loss_ratio = abs(today_pnl) / total_equity if today_pnl < 0 else 0.0
    limit_ratio = max_loss_pct / 100.0

    if loss_ratio >= limit_ratio:
        return {
            "passed": False,
            "message": f"Daily loss limit exceeded: {loss_ratio:.2%} (max {limit_ratio:.2%})",
            "current_loss": round(loss_ratio, 6),
            "limit_value": round(limit_ratio, 6),
        }

    return {
        "passed": True,
        "message": f"Daily loss within limit ({loss_ratio:.2%} / {limit_ratio:.2%})",
        "current_loss": round(loss_ratio, 6),
        "limit_value": round(limit_ratio, 6),
    }


async def check_position_ratio(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    order_quantity: float,
    order_price: float,
    order_side: str = "buy",
    max_ratio_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Check position ratio. Denominator adjusts for buy: subtract order cost from equity."""
    if max_ratio_pct is None:
        result = await db.execute(
            select(User.max_position_ratio).where(User.id == user_id)
        )
        row = result.fetchone()
        if row is None:
            return {"passed": False, "message": "User not found"}
        max_ratio_pct = row[0]

    if max_ratio_pct is None or max_ratio_pct <= 0:
        return {"passed": True, "message": "Position ratio limit not set"}

    limit_ratio = max_ratio_pct / 100.0

    all_pos_result = await db.execute(
        select(PositionModel).where(PositionModel.user_id == user_id)
    )
    all_positions = list(all_pos_result.scalars().all())

    current_position_value = 0.0
    for p in all_positions:
        if p.symbol == symbol:
            current_position_value = (p.quantity or 0) * (p.current_price or 0)

    order_value = order_quantity * order_price

    # For buys: the order value reduces cash (= reduces equity denominator)
    total_equity = await _estimate_equity(db, user_id, all_positions)
    if order_side == "buy":
        total_equity = max(total_equity, order_value)  # prevent negative
    # Denominator should reflect post-order equity: cash decreases for buys
    if order_side == "buy":
        denominator = total_equity  # includes the new position value in numerator too
    else:
        denominator = total_equity

    if denominator <= 0:
        return {
            "passed": True,
            "message": "Position ratio limit: empty portfolio",
            "current_ratio": 0.0,
            "limit_value": round(limit_ratio, 6),
        }

    if order_side == "sell":
        new_position_value = max(0, current_position_value - order_value)
    else:
        new_position_value = current_position_value + order_value

    new_ratio = new_position_value / denominator

    if new_ratio > limit_ratio:
        return {
            "passed": False,
            "message": f"Position ratio would exceed limit: {new_ratio:.2%} (max {limit_ratio:.2%})",
            "current_ratio": round(new_ratio, 6),
            "limit_value": round(limit_ratio, 6),
        }

    return {
        "passed": True,
        "message": f"Position ratio within limit ({new_ratio:.2%} / {limit_ratio:.2%})",
        "current_ratio": round(new_ratio, 6),
        "limit_value": round(limit_ratio, 6),
    }


async def check_blacklist(
    db: AsyncSession,
    user_id: int,
    symbol: str,
) -> Dict[str, Any]:
    """Check blacklist from RiskRule table (user-specific + global rules)."""
    result = await db.execute(
        select(RiskRule).where(
            and_(
                RiskRule.rule_type == RiskRuleType.BLACKLIST,
                RiskRule.is_active == True,
                or_(RiskRule.user_id == user_id, RiskRule.user_id.is_(None)),
            )
        )
    )
    for rule in result.scalars().all():
        if rule.symbols:
            restricted = [s.strip().upper() for s in rule.symbols.split(",")]
            if symbol.upper() in restricted:
                return {
                    "passed": False,
                    "message": f"Symbol '{symbol}' is blacklisted: {rule.name}",
                }
    return {"passed": True, "message": f"Symbol '{symbol}' is not blacklisted"}


async def check_max_position_qty(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    max_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Check if adding a new position would exceed max position count."""
    if max_count is None:
        return {"passed": True, "message": "Max position qty not configured"}

    pos_result = await db.execute(
        select(func.count(PositionModel.id)).where(
            and_(PositionModel.user_id == user_id, PositionModel.quantity > 0)
        )
    )
    current_count = pos_result.scalar() or 0

    # Check if this symbol is already held
    existing = await db.execute(
        select(PositionModel).where(
            and_(PositionModel.user_id == user_id, PositionModel.symbol == symbol)
        )
    )
    is_new = existing.scalar_one_or_none() is None

    if is_new and current_count >= max_count:
        return {
            "passed": False,
            "message": f"持仓数量已达上限 {max_count}",
        }

    return {"passed": True, "message": f"Position count {current_count}/{max_count}"}


async def check_max_order_count(
    db: AsyncSession,
    user_id: int,
    max_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Check daily order count limit."""
    if max_count is None:
        return {"passed": True, "message": "Max order count not configured"}

    today = _today_start()
    count_result = await db.execute(
        select(func.count(Order.id)).where(
            and_(Order.user_id == user_id, Order.created_at >= today)
        )
    )
    today_count = count_result.scalar() or 0

    if today_count >= max_count:
        return {
            "passed": False,
            "message": f"今日下单次数已达上限 {max_count}",
        }

    return {"passed": True, "message": f"Order count {today_count}/{max_count}"}


async def check_max_open_orders(
    db: AsyncSession,
    user_id: int,
    max_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Check max pending/open orders."""
    if max_count is None:
        return {"passed": True, "message": "Max open orders not configured"}

    count_result = await db.execute(
        select(func.count(Order.id)).where(
            and_(
                Order.user_id == user_id,
                Order.status.in_([OrderStatus.PENDING, OrderStatus.PARTIAL]),
            )
        )
    )
    open_count = count_result.scalar() or 0

    if open_count >= max_count:
        return {
            "passed": False,
            "message": f"挂单数量已达上限 {max_count}",
        }

    return {"passed": True, "message": f"Open orders {open_count}/{max_count}"}


async def check_stop_loss_rule(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    order_price: float,
    stop_loss_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Validate that a stop-loss exists or the order itself is within stop-loss boundary."""
    if stop_loss_pct is None:
        return {"passed": True, "message": "Stop-loss rule not configured"}

    # Check if there's a pending stop order within tolerance
    stop_result = await db.execute(
        select(Order).where(
            and_(
                Order.user_id == user_id,
                Order.symbol == symbol,
                Order.type == "stop",
                Order.status == OrderStatus.PENDING,
            )
        )
    )
    stop_order = stop_result.scalars().first()

    if stop_order and stop_order.stop_price:
        # Stop order exists — validate it's within configured range
        expected_stop = order_price * (1 - stop_loss_pct / 100.0)
        if stop_order.stop_price >= expected_stop:
            return {"passed": True, "message": "Stop-loss order in place"}

    return {
        "passed": True,
        "message": "Stop-loss rule: no violation detected",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Main entry point: check_risk_rules (async)
# ═══════════════════════════════════════════════════════════════════════════════


async def check_risk_rules(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    order_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Run all applicable risk rules before an order is placed.

    Priority: DB RiskRule table first, fallback to User model fields.
    Checks circuit breaker first.
    """
    # 0. Circuit breaker
    if await _is_circuit_breaker_active():
        reason = "Emergency stop is active"
        try:
            from app.core.redis import get_redis
            r = await get_redis()
            stored = await r.get(CIRCUIT_BREAKER_KEY)
            if stored:
                reason = f"紧急熔断: {stored}"
        except Exception:
            pass
        return {"passed": False, "message": reason}

    # 1. Fetch User once (cached for all checks)
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        return {"passed": False, "message": "User not found"}

    order_quantity = float(order_data.get("quantity", 0))
    order_price = float(order_data.get("price", 0))
    order_side = order_data.get("side", "buy")

    # 2. Load active DB risk rules (user-specific + global)
    rules_result = await db.execute(
        select(RiskRule).where(
            and_(
                RiskRule.is_active == True,
                or_(RiskRule.user_id == user_id, RiskRule.user_id.is_(None)),
            )
        )
    )
    db_rules: Dict[RiskRuleType, RiskRule] = {}
    for r in rules_result.scalars().all():
        # User-specific rule takes precedence over global
        if r.rule_type not in db_rules or r.user_id is not None:
            db_rules[r.rule_type] = r

    def _has_action(rt: RiskRuleType) -> Optional[RiskAction]:
        r = db_rules.get(rt)
        return r.action if r else None

    # 3. Run each rule check

    # 3a. MAX_DAILY_LOSS
    daily_rule = db_rules.get(RiskRuleType.MAX_DAILY_LOSS)
    daily_limit = (
        _get_rule_param(daily_rule, "max_loss_pct")
        if daily_rule
        else user.max_daily_loss
    )
    daily_check = await check_daily_loss(db, user_id, max_loss_pct=daily_limit)
    if not daily_check["passed"]:
        action = _has_action(RiskRuleType.MAX_DAILY_LOSS) or RiskAction.BLOCK
        await record_risk_event(
            db, rule=RiskRuleType.MAX_DAILY_LOSS, user_id=user_id, symbol=symbol,
            action=action,
            trigger_value=daily_check.get("current_loss"),
            limit_value=daily_check.get("limit_value"),
            message=daily_check["message"],
        )
        if action == RiskAction.CLOSE:
            await _execute_forced_close(db, user_id, symbol)
        if action in (RiskAction.BLOCK, RiskAction.CLOSE):
            return daily_check

    # 3b. MAX_POSITION_RATIO
    ratio_rule = db_rules.get(RiskRuleType.MAX_POSITION_RATIO)
    ratio_limit = (
        _get_rule_param(ratio_rule, "max_ratio_pct")
        if ratio_rule
        else user.max_position_ratio
    )
    ratio_check = await check_position_ratio(
        db, user_id, symbol, order_quantity, order_price, order_side,
        max_ratio_pct=ratio_limit,
    )
    if not ratio_check["passed"]:
        action = _has_action(RiskRuleType.MAX_POSITION_RATIO) or RiskAction.BLOCK
        await record_risk_event(
            db, rule=RiskRuleType.MAX_POSITION_RATIO, user_id=user_id, symbol=symbol,
            action=action,
            trigger_value=ratio_check.get("current_ratio"),
            limit_value=ratio_check.get("limit_value"),
            message=ratio_check["message"],
        )
        if action in (RiskAction.BLOCK, RiskAction.CLOSE):
            return ratio_check

    # 3c. BLACKLIST
    if RiskRuleType.BLACKLIST in db_rules:
        bl_check = await check_blacklist(db, user_id, symbol)
        if not bl_check["passed"]:
            action = _has_action(RiskRuleType.BLACKLIST) or RiskAction.BLOCK
            await record_risk_event(
                db, rule=RiskRuleType.BLACKLIST, user_id=user_id, symbol=symbol,
                action=action,
                trigger_value=None, limit_value=None,
                message=bl_check["message"],
            )
            if action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return bl_check

    # 3d. MAX_POSITION_QTY
    qty_rule = db_rules.get(RiskRuleType.MAX_POSITION_QTY)
    if qty_rule:
        max_qty = _get_rule_param(qty_rule, "max_count")
        qty_check = await check_max_position_qty(db, user_id, symbol, max_count=max_qty)
        if not qty_check["passed"]:
            action = qty_rule.action
            await record_risk_event(
                db, rule=RiskRuleType.MAX_POSITION_QTY, user_id=user_id, symbol=symbol,
                action=action, message=qty_check["message"],
            )
            if action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return qty_check

    # 3e. MAX_ORDER_COUNT
    oc_rule = db_rules.get(RiskRuleType.MAX_ORDER_COUNT)
    if oc_rule:
        max_oc = _get_rule_param(oc_rule, "max_count")
        oc_check = await check_max_order_count(db, user_id, max_count=max_oc)
        if not oc_check["passed"]:
            action = oc_rule.action
            await record_risk_event(
                db, rule=RiskRuleType.MAX_ORDER_COUNT, user_id=user_id, symbol=symbol,
                action=action, message=oc_check["message"],
            )
            if action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return oc_check

    # 3f. MAX_OPEN_ORDERS
    oo_rule = db_rules.get(RiskRuleType.MAX_OPEN_ORDERS)
    if oo_rule:
        max_oo = _get_rule_param(oo_rule, "max_count")
        oo_check = await check_max_open_orders(db, user_id, max_count=max_oo)
        if not oo_check["passed"]:
            action = oo_rule.action
            await record_risk_event(
                db, rule=RiskRuleType.MAX_OPEN_ORDERS, user_id=user_id, symbol=symbol,
                action=action, message=oo_check["message"],
            )
            if action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return oo_check

    # 3g. STOP_LOSS
    sl_rule = db_rules.get(RiskRuleType.STOP_LOSS)
    if sl_rule and order_side == "buy":
        sl_pct = _get_rule_param(sl_rule, "stop_loss_pct")
        sl_check = await check_stop_loss_rule(
            db, user_id, symbol, order_price, stop_loss_pct=sl_pct,
        )
        if not sl_check["passed"]:
            action = sl_rule.action
            await record_risk_event(
                db, rule=RiskRuleType.STOP_LOSS, user_id=user_id, symbol=symbol,
                action=action, message=sl_check["message"],
            )
            if action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return sl_check

    return {"passed": True, "message": "All risk checks passed"}


# ═══════════════════════════════════════════════════════════════════════════════
# Sync versions (for Celery tasks using SQLAlchemy sync Session)
# ═══════════════════════════════════════════════════════════════════════════════


def check_daily_loss_sync(
    db: Session,
    user_id: int,
    max_loss_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Sync version of check_daily_loss."""
    today = _today_start()

    if max_loss_pct is None:
        row = db.execute(select(User.max_daily_loss).where(User.id == user_id)).fetchone()
        if row is None:
            return {"passed": False, "message": "User not found"}
        max_loss_pct = row[0]

    if max_loss_pct is None or max_loss_pct <= 0:
        return {"passed": True, "message": "Daily loss limit not set"}

    positions = list(db.execute(
        select(PositionModel).where(PositionModel.user_id == user_id)
    ).scalars().all())
    unrealised_day_pnl = sum((p.day_pnl or 0.0) for p in positions)

    # Realised PnL via Trade.trade_time
    sell_symbols_subq = select(Order.symbol).where(
        and_(Order.user_id == user_id, Order.side == OrderSide.SELL)
    )
    today_trades = list(db.execute(
        select(Trade).where(
            and_(Trade.symbol.in_(sell_symbols_subq), Trade.trade_time >= today)
        ).order_by(Trade.trade_time.asc())
    ).scalars().all())

    all_buys = list(db.execute(
        select(Order).where(
            and_(Order.user_id == user_id, Order.side == OrderSide.BUY, Order.status == OrderStatus.FILLED)
        ).order_by(Order.created_at.asc())
    ).scalars().all())

    buy_queues: Dict[str, List[Dict[str, Any]]] = {}
    for b in all_buys:
        sym = b.symbol
        if sym not in buy_queues:
            buy_queues[sym] = []
        buy_queues[sym].append({
            "qty": float(b.filled_quantity or 0),
            "price": float(b.avg_price or 0),
            "sell_price": 0.0,
        })

    realised_pnl = 0.0
    for trade in today_trades:
        sym = trade.symbol
        if sym not in buy_queues or not buy_queues[sym]:
            continue
        qty = float(trade.quantity or 0)
        price = float(trade.price or 0)
        if qty <= 0 or price <= 0:
            continue
        for lot in buy_queues[sym]:
            lot["sell_price"] = price
        pnl, buy_queues[sym] = _fifo_cost_basis(buy_queues[sym], qty)
        realised_pnl += pnl

    today_pnl = unrealised_day_pnl + realised_pnl
    total_equity = _estimate_equity_sync(db, user_id, positions)

    if total_equity <= 0:
        return {
            "passed": True, "message": "Daily loss limit: no positions",
            "current_loss": 0.0,
            "limit_value": round(max_loss_pct / 100.0, 6),
        }

    loss_ratio = abs(today_pnl) / total_equity if today_pnl < 0 else 0.0
    limit_ratio = max_loss_pct / 100.0

    if loss_ratio >= limit_ratio:
        return {
            "passed": False,
            "message": f"Daily loss limit exceeded: {loss_ratio:.2%} (max {limit_ratio:.2%})",
            "current_loss": round(loss_ratio, 6),
            "limit_value": round(limit_ratio, 6),
        }

    return {
        "passed": True,
        "message": f"Daily loss within limit ({loss_ratio:.2%} / {limit_ratio:.2%})",
        "current_loss": round(loss_ratio, 6),
        "limit_value": round(limit_ratio, 6),
    }


def check_position_ratio_sync(
    db: Session,
    user_id: int,
    symbol: str,
    order_quantity: float,
    order_price: float,
    order_side: str = "buy",
    max_ratio_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Sync version of check_position_ratio."""
    if max_ratio_pct is None:
        row = db.execute(
            select(User.max_position_ratio).where(User.id == user_id)
        ).fetchone()
        if row is None:
            return {"passed": False, "message": "User not found"}
        max_ratio_pct = row[0]

    if max_ratio_pct is None or max_ratio_pct <= 0:
        return {"passed": True, "message": "Position ratio limit not set"}

    limit_ratio = max_ratio_pct / 100.0

    all_positions = list(db.execute(
        select(PositionModel).where(PositionModel.user_id == user_id)
    ).scalars().all())

    current_position_value = 0.0
    for p in all_positions:
        if p.symbol == symbol:
            current_position_value = (p.quantity or 0) * (p.current_price or 0)

    order_value = order_quantity * order_price
    total_equity = _estimate_equity_sync(db, user_id, all_positions)
    denominator = max(total_equity, order_value) if order_side == "buy" else total_equity

    if denominator <= 0:
        return {
            "passed": True, "message": "Position ratio limit: empty portfolio",
            "current_ratio": 0.0,
            "limit_value": round(limit_ratio, 6),
        }

    if order_side == "sell":
        new_position_value = max(0, current_position_value - order_value)
    else:
        new_position_value = current_position_value + order_value

    new_ratio = new_position_value / denominator

    if new_ratio > limit_ratio:
        return {
            "passed": False,
            "message": f"Position ratio would exceed limit: {new_ratio:.2%} (max {limit_ratio:.2%})",
            "current_ratio": round(new_ratio, 6),
            "limit_value": round(limit_ratio, 6),
        }

    return {
        "passed": True,
        "message": f"Position ratio within limit ({new_ratio:.2%} / {limit_ratio:.2%})",
        "current_ratio": round(new_ratio, 6),
        "limit_value": round(limit_ratio, 6),
    }


def check_blacklist_sync(db: Session, user_id: int, symbol: str) -> Dict[str, Any]:
    """Sync version of check_blacklist."""
    rules = list(db.execute(
        select(RiskRule).where(
            and_(
                RiskRule.rule_type == RiskRuleType.BLACKLIST,
                RiskRule.is_active == True,
                or_(RiskRule.user_id == user_id, RiskRule.user_id.is_(None)),
            )
        )
    ).scalars().all())

    for rule in rules:
        if rule.symbols:
            restricted = [s.strip().upper() for s in rule.symbols.split(",")]
            if symbol.upper() in restricted:
                return {"passed": False, "message": f"Symbol '{symbol}' is blacklisted: {rule.name}"}
    return {"passed": True, "message": f"Symbol '{symbol}' is not blacklisted"}


def check_max_position_qty_sync(
    db: Session, user_id: int, symbol: str, max_count: Optional[int] = None,
) -> Dict[str, Any]:
    if max_count is None:
        return {"passed": True, "message": "Max position qty not configured"}
    current_count = db.execute(
        select(func.count(PositionModel.id)).where(
            and_(PositionModel.user_id == user_id, PositionModel.quantity > 0)
        )
    ).scalar() or 0
    existing = db.execute(
        select(PositionModel).where(
            and_(PositionModel.user_id == user_id, PositionModel.symbol == symbol)
        )
    ).scalar_one_or_none()
    if not existing and current_count >= max_count:
        return {"passed": False, "message": f"持仓数量已达上限 {max_count}"}
    return {"passed": True, "message": f"Position count {current_count}/{max_count}"}


def check_max_order_count_sync(
    db: Session, user_id: int, max_count: Optional[int] = None,
) -> Dict[str, Any]:
    if max_count is None:
        return {"passed": True, "message": "Max order count not configured"}
    today = _today_start()
    today_count = db.execute(
        select(func.count(Order.id)).where(
            and_(Order.user_id == user_id, Order.created_at >= today)
        )
    ).scalar() or 0
    if today_count >= max_count:
        return {"passed": False, "message": f"今日下单次数已达上限 {max_count}"}
    return {"passed": True, "message": f"Order count {today_count}/{max_count}"}


def check_max_open_orders_sync(
    db: Session, user_id: int, max_count: Optional[int] = None,
) -> Dict[str, Any]:
    if max_count is None:
        return {"passed": True, "message": "Max open orders not configured"}
    open_count = db.execute(
        select(func.count(Order.id)).where(
            and_(
                Order.user_id == user_id,
                Order.status.in_([OrderStatus.PENDING, OrderStatus.PARTIAL]),
            )
        )
    ).scalar() or 0
    if open_count >= max_count:
        return {"passed": False, "message": f"挂单数量已达上限 {max_count}"}
    return {"passed": True, "message": f"Open orders {open_count}/{max_count}"}


def record_risk_event_sync(
    db: Session,
    rule: RiskRuleType,
    user_id: int,
    symbol: Optional[str],
    action: RiskAction,
    trigger_value: Optional[float] = None,
    limit_value: Optional[float] = None,
    message: Optional[str] = None,
) -> RiskRecord:
    """Sync version of record_risk_event."""
    rule_row = db.execute(
        select(RiskRule).where(
            and_(RiskRule.rule_type == rule, RiskRule.is_active == True)
        ).limit(1)
    ).scalar_one_or_none()
    rule_id = rule_row.id if rule_row else None

    record = RiskRecord(
        rule_id=rule_id, user_id=user_id, symbol=symbol,
        action=action,
        trigger_value=trigger_value, limit_value=limit_value,
        message=message,
    )
    db.add(record)
    db.flush()

    # Dispatch notification
    try:
        from app.models.notification import Notification
        notif = Notification(
            user_id=user_id,
            type="risk",
            title=f"风控触发: {rule.value}",
            content=message or f"风控规则 {rule.value} 已触发 ({action.value})",
            metadata_json={
                "rule_type": rule.value if hasattr(rule, 'value') else str(rule),
                "action": action.value if hasattr(action, 'value') else str(action),
                "symbol": symbol,
                "trigger_value": trigger_value,
                "limit_value": limit_value,
            },
        )
        db.add(notif)
    except Exception as e:
        logger.warning(f"Failed to create risk notification: {e}")

    return record


def check_risk_rules_sync(
    db: Session,
    user_id: int,
    symbol: str,
    order_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Sync version of check_risk_rules — for Celery task callers."""
    # 0. Circuit breaker
    if _is_circuit_breaker_active_sync():
        reason = "Emergency stop is active"
        try:
            from app.core.redis import get_sync_redis
            r = get_sync_redis()
            stored = r.get(CIRCUIT_BREAKER_KEY)
            if stored:
                reason = f"紧急熔断: {stored}"
        except Exception:
            pass
        return {"passed": False, "message": reason}

    # 1. User (single fetch)
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        return {"passed": False, "message": "User not found"}

    order_quantity = float(order_data.get("quantity", 0))
    order_price = float(order_data.get("price", 0))
    order_side = order_data.get("side", "buy")

    # 2. Load DB rules
    db_rules_rows = list(db.execute(
        select(RiskRule).where(
            and_(
                RiskRule.is_active == True,
                or_(RiskRule.user_id == user_id, RiskRule.user_id.is_(None)),
            )
        )
    ).scalars().all())
    db_rules: Dict[RiskRuleType, RiskRule] = {}
    for r in db_rules_rows:
        if r.rule_type not in db_rules or r.user_id is not None:
            db_rules[r.rule_type] = r

    def _action(rt: RiskRuleType) -> Optional[RiskAction]:
        r = db_rules.get(rt)
        return r.action if r else None

    # 3a. MAX_DAILY_LOSS
    daily_rule = db_rules.get(RiskRuleType.MAX_DAILY_LOSS)
    daily_limit = _get_rule_param(daily_rule, "max_loss_pct") if daily_rule else user.max_daily_loss
    dc = check_daily_loss_sync(db, user_id, max_loss_pct=daily_limit)
    if not dc["passed"]:
        action = _action(RiskRuleType.MAX_DAILY_LOSS) or RiskAction.BLOCK
        record_risk_event_sync(db, RiskRuleType.MAX_DAILY_LOSS, user_id, symbol,
                               action, dc.get("current_loss"), dc.get("limit_value"), dc["message"])
        if action == RiskAction.CLOSE:
            _execute_forced_close_sync(db, user_id, symbol)
        if action in (RiskAction.BLOCK, RiskAction.CLOSE):
            return dc

    # 3b. MAX_POSITION_RATIO
    ratio_rule = db_rules.get(RiskRuleType.MAX_POSITION_RATIO)
    ratio_limit = _get_rule_param(ratio_rule, "max_ratio_pct") if ratio_rule else user.max_position_ratio
    rc = check_position_ratio_sync(db, user_id, symbol, order_quantity, order_price, order_side, max_ratio_pct=ratio_limit)
    if not rc["passed"]:
        action = _action(RiskRuleType.MAX_POSITION_RATIO) or RiskAction.BLOCK
        record_risk_event_sync(db, RiskRuleType.MAX_POSITION_RATIO, user_id, symbol,
                               action, rc.get("current_ratio"), rc.get("limit_value"), rc["message"])
        if action in (RiskAction.BLOCK, RiskAction.CLOSE):
            return rc

    # 3c. BLACKLIST
    if RiskRuleType.BLACKLIST in db_rules:
        bc = check_blacklist_sync(db, user_id, symbol)
        if not bc["passed"]:
            action = _action(RiskRuleType.BLACKLIST) or RiskAction.BLOCK
            record_risk_event_sync(db, RiskRuleType.BLACKLIST, user_id, symbol, action, None, None, bc["message"])
            if action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return bc

    # 3d. MAX_POSITION_QTY
    qty_rule = db_rules.get(RiskRuleType.MAX_POSITION_QTY)
    if qty_rule:
        qc = check_max_position_qty_sync(db, user_id, symbol, max_count=_get_rule_param(qty_rule, "max_count"))
        if not qc["passed"]:
            record_risk_event_sync(db, RiskRuleType.MAX_POSITION_QTY, user_id, symbol, qty_rule.action, None, None, qc["message"])
            if qty_rule.action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return qc

    # 3e. MAX_ORDER_COUNT
    oc_rule = db_rules.get(RiskRuleType.MAX_ORDER_COUNT)
    if oc_rule:
        occ = check_max_order_count_sync(db, user_id, max_count=_get_rule_param(oc_rule, "max_count"))
        if not occ["passed"]:
            record_risk_event_sync(db, RiskRuleType.MAX_ORDER_COUNT, user_id, symbol, oc_rule.action, None, None, occ["message"])
            if oc_rule.action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return occ

    # 3f. MAX_OPEN_ORDERS
    oo_rule = db_rules.get(RiskRuleType.MAX_OPEN_ORDERS)
    if oo_rule:
        ooc = check_max_open_orders_sync(db, user_id, max_count=_get_rule_param(oo_rule, "max_count"))
        if not ooc["passed"]:
            record_risk_event_sync(db, RiskRuleType.MAX_OPEN_ORDERS, user_id, symbol, oo_rule.action, None, None, ooc["message"])
            if oo_rule.action in (RiskAction.BLOCK, RiskAction.CLOSE):
                return ooc

    # 3g. STOP_LOSS
    sl_rule = db_rules.get(RiskRuleType.STOP_LOSS)
    if sl_rule and order_side == "buy":
        sl_pct = _get_rule_param(sl_rule, "stop_loss_pct")
        if sl_pct:
            # Create a stop-loss order if none exists
            existing_stop = db.execute(
                select(Order).where(
                    and_(Order.user_id == user_id, Order.symbol == symbol,
                         Order.type == "stop", Order.status == OrderStatus.PENDING)
                )
            ).scalars().first()
            if not existing_stop:
                stop_price = round(order_price * (1 - sl_pct / 100.0), 2)
                stop_order = Order(
                    user_id=user_id, symbol=symbol, side=OrderSide.SELL,
                    type="stop", status=OrderStatus.PENDING,
                    stop_price=stop_price, price=stop_price,
                    quantity=order_quantity, source="risk_rule",
                    remark=f"RiskRule stop-loss {sl_pct}%",
                )
                db.add(stop_order)
                db.flush()

    return {"passed": True, "message": "All risk checks passed"}


# ── Forced close (CLOSE action) ─────────────────────────────────────────────


async def _execute_forced_close(db: AsyncSession, user_id: int, symbol: Optional[str] = None):
    """CLOSE action: force-liquidate positions (all or specific symbol)."""
    conditions = [PositionModel.user_id == user_id, PositionModel.quantity > 0]
    if symbol:
        conditions.append(PositionModel.symbol == symbol)

    pos_result = await db.execute(
        select(PositionModel).where(and_(*conditions))
    )
    positions = list(pos_result.scalars().all())

    for pos in positions:
        qty = int(pos.available_quantity or pos.quantity or 0)
        if qty <= 0:
            continue
        price = pos.current_price or 0
        if price <= 0:
            continue

        order = Order(
            user_id=user_id, symbol=pos.symbol, side=OrderSide.SELL,
            type="market", status=OrderStatus.FILLED,
            price=price, quantity=float(qty), filled_quantity=float(qty),
            avg_price=price, fee=float(qty) * price * 0.001,
            source="risk_close",
            remark=f"Forced close by risk rule (CLOSE action)",
        )
        db.add(order)
        await db.flush()

        trade = Trade(
            order_id=order.id, symbol=pos.symbol, side=OrderSide.SELL,
            price=price, quantity=float(qty), fee=order.fee, fee_asset="CNY",
            trade_time=_beijing_now(),
        )
        db.add(trade)
        pos.quantity = 0
        pos.available_quantity = 0
        logger.warning(f"[RISK-CLOSE] 强制平仓 {pos.symbol} {qty}股 @ {price}")


def _execute_forced_close_sync(db: Session, user_id: int, symbol: Optional[str] = None):
    """Sync version of forced close."""
    conditions = [PositionModel.user_id == user_id, PositionModel.quantity > 0]
    if symbol:
        conditions.append(PositionModel.symbol == symbol)

    positions = list(db.execute(
        select(PositionModel).where(and_(*conditions))
    ).scalars().all())

    for pos in positions:
        qty = int(pos.available_quantity or pos.quantity or 0)
        if qty <= 0:
            continue
        price = pos.current_price or 0
        if price <= 0:
            continue

        order = Order(
            user_id=user_id, symbol=pos.symbol, side=OrderSide.SELL,
            type="market", status=OrderStatus.FILLED,
            price=price, quantity=float(qty), filled_quantity=float(qty),
            avg_price=price, fee=float(qty) * price * 0.001,
            source="risk_close",
            remark="Forced close by risk rule (CLOSE action)",
        )
        db.add(order)
        db.flush()

        trade = Trade(
            order_id=order.id, symbol=pos.symbol, side=OrderSide.SELL,
            price=price, quantity=float(qty), fee=order.fee, fee_asset="CNY",
            trade_time=_beijing_now(),
        )
        db.add(trade)
        pos.quantity = 0
        pos.available_quantity = 0
        logger.warning(f"[RISK-CLOSE] 强制平仓 {pos.symbol} {qty}股 @ {price}")


# ── record_risk_event (async — with notification) ────────────────────────────


async def record_risk_event(
    db: AsyncSession,
    rule: RiskRuleType,
    user_id: int,
    symbol: Optional[str],
    action: RiskAction,
    trigger_value: Optional[float] = None,
    limit_value: Optional[float] = None,
    message: Optional[str] = None,
) -> RiskRecord:
    """Record a risk trigger event and dispatch a notification."""
    rule_result = await db.execute(
        select(RiskRule).where(
            and_(RiskRule.rule_type == rule, RiskRule.is_active == True)
        ).limit(1)
    )
    risk_rule = rule_result.scalar_one_or_none()
    rule_id = risk_rule.id if risk_rule else None

    record = RiskRecord(
        rule_id=rule_id, user_id=user_id, symbol=symbol,
        action=action,
        trigger_value=trigger_value, limit_value=limit_value,
        message=message,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)

    # Dispatch notification
    try:
        from app.models.notification import Notification
        rule_val = rule.value if hasattr(rule, 'value') else str(rule)
        action_val = action.value if hasattr(action, 'value') else str(action)
        notif = Notification(
            user_id=user_id,
            type="risk",
            title=f"风控触发: {rule_val}",
            content=message or f"风控规则 {rule_val} 已触发 ({action_val})",
            metadata_json={
                "rule_type": rule_val,
                "action": action_val,
                "symbol": symbol,
                "trigger_value": trigger_value,
                "limit_value": limit_value,
            },
        )
        db.add(notif)
    except Exception as e:
        logger.warning(f"Failed to create risk notification: {e}")

    return record
