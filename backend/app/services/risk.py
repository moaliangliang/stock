"""
Risk control service - pre-trade risk checks and event logging."""
from __future__ import annotations
import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.order import Order, OrderSide, OrderStatus
from app.models.position import Position as PositionModel
from app.models.risk import RiskAction, RiskRecord, RiskRule, RiskRuleType
from app.models.user import User


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def check_risk_rules(
    db: AsyncSession,
    user_id: int,
    symbol: str,
    order_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run all applicable risk rules before an order is placed.

    This is the main entry point for pre-trade risk checks. It evaluates
    daily loss limits, position ratio limits, and symbol blacklists.

    Args:
        db: Database session.
        user_id: ID of the user placing the order.
        symbol: Trading symbol.
        order_data: Order details including quantity, price, and side.

    Returns:
        A dict with:
            - passed (bool): True if all checks pass.
            - message (str): Description of the first failed check, or
              "All risk checks passed".
    """
    order_quantity = float(order_data.get("quantity", 0))
    order_price = float(order_data.get("price", 0))
    order_side = order_data.get("side", "buy")

    # 1. Check max daily loss
    daily_check = await check_daily_loss(db, user_id)
    if not daily_check["passed"]:
        await record_risk_event(
            db,
            rule=RiskRuleType.MAX_DAILY_LOSS,
            user_id=user_id,
            symbol=symbol,
            action=RiskAction.BLOCK,
            trigger_value=daily_check.get("current_loss", 0),
            limit_value=daily_check.get("limit_value"),
            message=daily_check["message"],
        )
        return daily_check

    # 2. Check position ratio
    position_check = await check_position_ratio(
        db, user_id, symbol, order_quantity, order_price
    )
    if not position_check["passed"]:
        await record_risk_event(
            db,
            rule=RiskRuleType.MAX_POSITION_RATIO,
            user_id=user_id,
            symbol=symbol,
            action=RiskAction.BLOCK,
            trigger_value=position_check.get("current_ratio", 0),
            limit_value=position_check.get("limit_value"),
            message=position_check["message"],
        )
        return position_check

    # 3. Check blacklist
    blacklist_check = await check_blacklist(db, user_id, symbol)
    if not blacklist_check["passed"]:
        await record_risk_event(
            db,
            rule=RiskRuleType.BLACKLIST,
            user_id=user_id,
            symbol=symbol,
            action=RiskAction.BLOCK,
            trigger_value=None,
            limit_value=None,
            message=blacklist_check["message"],
        )
        return blacklist_check

    return {"passed": True, "message": "All risk checks passed"}


async def check_daily_loss(
    db: AsyncSession,
    user_id: int,
) -> Dict[str, Any]:
    """
    Check whether the user has exceeded their maximum daily loss limit.

    The daily loss is calculated as the sum of realised PnL from filled
    orders today. A negative sum indicates a net loss.

    Args:
        db: Database session.
        user_id: ID of the user.

    Returns:
        A dict with passed (bool), message (str), current_loss (float),
        and limit_value (float).
    """
    # Get user's daily loss limit
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return {"passed": False, "message": "User not found"}

    max_loss_pct = user.max_daily_loss  # e.g. 5 %
    if max_loss_pct is None or max_loss_pct <= 0:
        return {"passed": True, "message": "Daily loss limit not set"}

    # Calculate today's total portfolio value change
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Sum of realised PnL from today's trades
    today_trades_result = await db.execute(
        select(func.sum(Order.filled_quantity * (Order.avg_price - Order.price)))
        .where(
            and_(
                Order.user_id == user_id,
                Order.status == OrderStatus.FILLED,
                Order.updated_at >= today_start,
            )
        )
    )
    today_pnl = today_trades_result.scalar() or 0.0

    # Calculate total investment as reference
    total_investment_result = await db.execute(
        select(func.sum(PositionModel.cost_price * PositionModel.quantity))
        .where(PositionModel.user_id == user_id)
    )
    total_investment = total_investment_result.scalar() or 1.0
    if total_investment <= 0:
        total_investment = 1.0

    loss_ratio = abs(today_pnl) / total_investment if today_pnl < 0 else 0.0
    limit_ratio = max_loss_pct / 100.0

    if loss_ratio >= limit_ratio:
        return {
            "passed": False,
            "message": (
                f"Daily loss limit exceeded: {loss_ratio:.2%} "
                f"(max {limit_ratio:.2%})"
            ),
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
) -> Dict[str, Any]:
    """
    Check whether the order would exceed the maximum allowed position ratio.

    The position ratio is calculated as the total position value
    (including this order) divided by the total portfolio value.

    Args:
        db: Database session.
        user_id: ID of the user.
        symbol: Trading symbol.
        order_quantity: Quantity being ordered.
        order_price: Expected fill price.

    Returns:
        A dict with passed (bool), message (str), current_ratio (float),
        and limit_value (float).
    """
    # Get user's max position ratio
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        return {"passed": False, "message": "User not found"}

    max_ratio_pct = user.max_position_ratio  # e.g. 30
    if max_ratio_pct is None or max_ratio_pct <= 0:
        return {"passed": True, "message": "Position ratio limit not set"}

    limit_ratio = max_ratio_pct / 100.0

    # Get current position value for this symbol
    position_result = await db.execute(
        select(PositionModel).where(
            and_(
                PositionModel.user_id == user_id,
                PositionModel.symbol == symbol,
            )
        )
    )
    position = position_result.scalar_one_or_none()
    current_position_value = (
        position.quantity * position.current_price if position else 0.0
    )

    # Get total portfolio value (positions + cash approximation)
    # Use total position value across all symbols as denominator
    all_positions_result = await db.execute(
        select(PositionModel).where(PositionModel.user_id == user_id)
    )
    all_positions = list(all_positions_result.scalars().all())
    total_position_value = sum(
        p.quantity * p.current_price for p in all_positions
    ) or 1.0

    # Calculate new position value after this order
    order_value = order_quantity * order_price
    new_position_value = current_position_value + order_value

    new_ratio = new_position_value / total_position_value

    if new_ratio > limit_ratio:
        return {
            "passed": False,
            "message": (
                f"Position ratio would exceed limit: {new_ratio:.2%} "
                f"(max {limit_ratio:.2%})"
            ),
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
    """
    Check if the symbol is blacklisted for the user (or globally).

    Blacklist rules have rule_type = BLACKLIST and store the restricted
    symbols in their *symbols* field (comma-separated).

    Args:
        db: Database session.
        user_id: ID of the user.

    Returns:
        A dict with passed (bool) and message (str).
    """
    from sqlalchemy import or_

    result = await db.execute(
        select(RiskRule).where(
            and_(
                RiskRule.rule_type == RiskRuleType.BLACKLIST,
                RiskRule.is_active == True,
                or_(RiskRule.user_id == user_id, RiskRule.user_id.is_(None)),
            )
        )
    )
    all_rules = list(result.scalars().all())

    for rule in all_rules:
        if rule.symbols:
            restricted_symbols = [
                s.strip().upper() for s in rule.symbols.split(",")
            ]
            if symbol.upper() in restricted_symbols:
                return {
                    "passed": False,
                    "message": f"Symbol '{symbol}' is blacklisted: {rule.name}",
                }

    return {"passed": True, "message": f"Symbol '{symbol}' is not blacklisted"}


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
    """
    Record a risk rule trigger event in the database.

    Args:
        db: Database session.
        rule: The risk rule type that was triggered.
        user_id: ID of the affected user.
        symbol: Related trading symbol (may be None).
        action: Action taken (WARN, BLOCK, CLOSE).
        trigger_value: The value that triggered the rule.
        limit_value: The configured limit value.
        message: Human-readable description of the event.

    Returns:
        The created RiskRecord object.
    """
    # Find the matching rule definition (first match)
    rule_result = await db.execute(
        select(RiskRule).where(
            and_(
                RiskRule.rule_type == rule,
                RiskRule.is_active == True,
            )
        ).limit(1)
    )
    risk_rule = rule_result.scalar_one_or_none()
    rule_id = risk_rule.id if risk_rule else None

    record = RiskRecord(
        rule_id=rule_id,
        user_id=user_id,
        symbol=symbol,
        action=action,
        trigger_value=trigger_value,
        limit_value=limit_value,
        message=message,
    )
    db.add(record)
    await db.flush()
    await db.refresh(record)
    return record


async def get_risk_rules(
    db: AsyncSession,
    user_id: Optional[int] = None,
    rule_type: Optional[RiskRuleType] = None,
    is_active: Optional[bool] = None,
    skip: int = 0,
    limit: int = 100,
) -> list[RiskRule]:
    """获取风控规则列表"""
    conditions = []
    if user_id is not None:
        conditions.append(RiskRule.user_id == user_id)
    if rule_type is not None:
        conditions.append(RiskRule.rule_type == rule_type)
    if is_active is not None:
        conditions.append(RiskRule.is_active == is_active)

    result = await db.execute(
        select(RiskRule).where(and_(*conditions) if conditions else True).offset(skip).limit(limit).order_by(RiskRule.created_at.desc())
    )
    return list(result.scalars().all())


async def create_risk_rule(db: AsyncSession, user_id: Optional[int], rule_data: dict) -> RiskRule:
    """创建风控规则"""
    params = rule_data.get("params", "{}")
    if isinstance(params, dict):
        params = json.dumps(params)
    symbols = rule_data.get("symbols")
    if isinstance(symbols, list):
        symbols = json.dumps(symbols)

    rule = RiskRule(
        user_id=user_id,
        name=rule_data["name"],
        rule_type=rule_data["rule_type"],
        action=rule_data.get("action", RiskAction.WARN),
        is_active=rule_data.get("is_active", True),
        params=params,
        symbols=symbols,
        description=rule_data.get("description"),
    )
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


async def update_risk_rule(db: AsyncSession, rule_id: int, rule_data: dict) -> Optional[RiskRule]:
    """更新风控规则"""
    result = await db.execute(select(RiskRule).where(RiskRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return None
    for field in ["name", "rule_type", "action", "is_active", "params", "symbols", "description"]:
        if field in rule_data:
            value = rule_data[field]
            if field in ("params", "symbols") and isinstance(value, (dict, list)):
                value = json.dumps(value)
            setattr(rule, field, value)
    await db.flush()
    await db.refresh(rule)
    return rule


async def delete_risk_rule(db: AsyncSession, rule_id: int) -> bool:
    """删除风控规则"""
    result = await db.execute(select(RiskRule).where(RiskRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if not rule:
        return False
    await db.delete(rule)
    await db.flush()
    return True


async def get_risk_records(db: AsyncSession, user_id: Optional[int] = None, skip: int = 0, limit: int = 100) -> list[RiskRecord]:
    """获取风控触发记录"""
    conditions = []
    if user_id is not None:
        conditions.append(RiskRecord.user_id == user_id)
    result = await db.execute(
        select(RiskRecord).where(and_(*conditions) if conditions else True).order_by(RiskRecord.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())
