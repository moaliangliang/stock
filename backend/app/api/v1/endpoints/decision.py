"""
Investment decision API endpoints.
"""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.decision import DecisionOutcome, InvestmentDecision
from app.models.user import User
from app.schemas.common import Response
from app.schemas.decision import (
    DecisionGenerateRequest,
    DecisionResponse,
    DecisionSummaryResponse,
    DecisionOutcomeResponse,
    OutcomeSummaryResponse,
)
from app.services import decision as decision_service

router = APIRouter(prefix="/decisions", tags=["投资决策"])


@router.post("/generate", response_model=Response)
async def generate_decisions(
    req: DecisionGenerateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量生成投资决策建议"""
    results = await decision_service.generate_decisions_batch(
        db, current_user.id, req.symbols
    )
    return Response(data=results, message=f"已生成{len(results)}条决策建议")


@router.get("", response_model=Response)
async def list_decisions(
    status: Optional[str] = Query(None, description="状态过滤"),
    symbol: Optional[str] = Query(None, description="股票代码过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查询投资决策列表"""
    items, total = await decision_service.get_decisions(
        db, current_user.id, status=status, symbol=symbol, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size
    return Response(data={
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    })


@router.get("/summary", response_model=Response)
async def decision_summary(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """投资决策仪表盘汇总"""
    summary = await decision_service.get_decision_summary(db, current_user.id)
    return Response(data=summary)


@router.get("/{decision_id}", response_model=Response)
async def get_decision(
    decision_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单条决策详情"""
    decision = await decision_service.get_decision(db, decision_id, current_user.id)
    if not decision:
        return Response(code=404, message="决策记录不存在")
    return Response(data=decision)


@router.put("/{decision_id}/execute", response_model=Response)
async def execute_decision(
    decision_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """执行决策建议"""
    decision = await decision_service.execute_decision(db, decision_id, current_user.id)
    if not decision:
        return Response(code=404, message="决策记录不存在")
    return Response(data=decision, message="已标记为执行")


@router.put("/{decision_id}/dismiss", response_model=Response)
async def dismiss_decision(
    decision_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """忽略决策建议"""
    decision = await decision_service.dismiss_decision(db, decision_id, current_user.id)
    if not decision:
        return Response(code=404, message="决策记录不存在")
    return Response(data=decision, message="已忽略该建议")


# ---------------------------------------------------------------------------
# Outcome tracking endpoints (P3)
# ---------------------------------------------------------------------------


@router.get("/outcomes/summary", response_model=Response)
async def get_outcome_summary(
    days: int = Query(30, ge=1, le=365, description="统计天数"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取投资决策准确率统计"""
    # Count outcomes by type
    outcome_result = await db.execute(
        select(
            func.count(DecisionOutcome.id).label("total"),
            func.sum(case((DecisionOutcome.outcome == "win", 1), else_=0)).label("wins"),
            func.sum(case((DecisionOutcome.outcome == "loss", 1), else_=0)).label("losses"),
            func.sum(case((DecisionOutcome.outcome == "breakeven", 1), else_=0)).label("breakeven"),
        ).select_from(DecisionOutcome).join(
            InvestmentDecision,
            DecisionOutcome.decision_id == InvestmentDecision.id,
        ).where(
            InvestmentDecision.user_id == current_user.id,
        )
    )
    row = outcome_result.one()
    total = row.total or 0
    wins = row.wins or 0
    losses = row.losses or 0
    breakeven_count = row.breakeven or 0
    win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

    # Average PnL
    avg_pnl_result = await db.execute(
        select(func.avg(DecisionOutcome.pnl_pct)).select_from(DecisionOutcome).join(
            InvestmentDecision, DecisionOutcome.decision_id == InvestmentDecision.id
        ).where(
            InvestmentDecision.user_id == current_user.id,
            DecisionOutcome.pnl_pct.isnot(None),
        )
    )
    avg_pnl_pct = round(float(avg_pnl_result.scalar() or 0), 2)

    # Accuracy by recommendation type
    rec_accuracies: Dict[str, float] = {}
    for rec in ["strong_buy", "buy", "hold", "sell", "strong_sell"]:
        rec_result = await db.execute(
            select(func.count(DecisionOutcome.id)).select_from(DecisionOutcome).join(
                InvestmentDecision, DecisionOutcome.decision_id == InvestmentDecision.id
            ).where(
                InvestmentDecision.user_id == current_user.id,
                DecisionOutcome.recommendation == rec,
            )
        )
        rec_total = rec_result.scalar() or 0
        if rec_total > 0:
            rec_wins = await db.execute(
                select(func.count(DecisionOutcome.id)).select_from(DecisionOutcome).join(
                    InvestmentDecision, DecisionOutcome.decision_id == InvestmentDecision.id
                ).where(
                    InvestmentDecision.user_id == current_user.id,
                    DecisionOutcome.recommendation == rec,
                    DecisionOutcome.outcome == "win",
                )
            )
            rec_accuracies[rec] = round((rec_wins.scalar() or 0) / rec_total * 100, 1)
        else:
            rec_accuracies[rec] = 0.0

    # Recent outcomes
    recent_result = await db.execute(
        select(DecisionOutcome).join(
            InvestmentDecision, DecisionOutcome.decision_id == InvestmentDecision.id
        ).where(
            InvestmentDecision.user_id == current_user.id,
        ).order_by(desc(DecisionOutcome.checked_at)).limit(10)
    )
    recent_outcomes = [_outcome_to_dict(o) for o in recent_result.scalars().all()]

    data = {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakeven_count": breakeven_count,
        "win_rate": win_rate,
        "avg_pnl_pct": avg_pnl_pct,
        "strong_buy_accuracy": rec_accuracies.get("strong_buy", 0.0),
        "buy_accuracy": rec_accuracies.get("buy", 0.0),
        "hold_accuracy": rec_accuracies.get("hold", 0.0),
        "sell_accuracy": rec_accuracies.get("sell", 0.0),
        "strong_sell_accuracy": rec_accuracies.get("strong_sell", 0.0),
        "recent_outcomes": recent_outcomes,
    }
    return Response(data=data)


def _outcome_to_dict(outcome: DecisionOutcome) -> dict:
    return {
        "id": outcome.id,
        "decision_id": outcome.decision_id,
        "symbol": outcome.symbol,
        "recommendation": outcome.recommendation,
        "confidence": outcome.confidence,
        "entry_price": outcome.entry_price,
        "actual_high_24h": outcome.actual_high_24h,
        "actual_low_24h": outcome.actual_low_24h,
        "actual_close_24h": outcome.actual_close_24h,
        "hit_target": outcome.hit_target,
        "hit_stop": outcome.hit_stop,
        "pnl_pct": outcome.pnl_pct,
        "outcome": outcome.outcome.value if hasattr(outcome.outcome, "value") else outcome.outcome,
        "checked_at": outcome.checked_at.isoformat() if outcome.checked_at else None,
    }
