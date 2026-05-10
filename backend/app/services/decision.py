"""
Investment decision service - multi-factor scoring engine and CRUD.

Scoring methodology:
  Technical     — MA, MACD, RSI, Bollinger, KDJ, volume divergence
  Sentiment     — price change, volume, bid/ask spread
  Risk          — position exposure, drawdown, volatility, risk events
  Momentum      — short-term price trend strength, MA alignment
  Fundamental   — PE, PB, ROE, revenue growth

Dynamic weights driven by market regime (ADX-based classification).
Multi-timeframe confirmation via weekly kline analysis.
Tanh-normalized signal aggregation to prevent signal flooding.

Uses numpy for numerical computation (no pandas dependency).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import DecisionRecommendation, DecisionStatus, InvestmentDecision
from app.models.market_data import KLine, Ticker
from app.models.position import Position
from app.models.risk import RiskRecord
from app.models.user import User
from app.services.market import get_kline_data, get_ticker, save_kline_data
from app.services.notification import create_notification
from app.services.decision_config import get as _cfg


# ---------------------------------------------------------------------------
# Market regime weight maps (kept as fallback / reference)
# ---------------------------------------------------------------------------

# Default weights — used when continuous weight computation is unavailable
DEFAULT_WEIGHTS: Dict[str, float] = {
    "technical": _cfg("weights", "default_technical") or 0.35,
    "sentiment": _cfg("weights", "default_sentiment") or 0.20,
    "risk": _cfg("weights", "default_risk") or 0.25,
    "momentum": _cfg("weights", "default_momentum") or 0.10,
    "fundamental": _cfg("weights", "default_fundamental") or 0.10,
}


def _compute_dynamic_weights(adx: float, bb_width: float,
                              current_close: float, ma20: float) -> Dict[str, float]:
    """
    Compute continuous dynamic factor weights using sigmoid transitions.

    Eliminates the hard boundary problem of categorical regimes — when ADX
    moves from 24.9 to 25.1 the weights change smoothly rather than jumping
    to a completely different set.

    Key principles:
      - Higher ADX → more weight on momentum (trend-following works)
      - Higher volatility (BB width) → more weight on risk + sentiment
      - Uptrend → slight tilt toward momentum; downtrend → tilt toward sentiment
      - Technical analysis always maintains a significant base weight
    """
    # Sigmoid: maps any real value to (0, 1) smoothly
    def _sigmoid(x: float, k: float = 1.0) -> float:
        try:
            return 1.0 / (1.0 + math.exp(-k * x))
        except OverflowError:
            return 0.0 if x < 0 else 1.0

    # Trend strength: 0 = ranging, 1 = strong trending
    # Centered at ADX=25, steepness controls transition sharpness
    adx_center = _cfg("regime_detection", "adx_center") or 25.0
    adx_k = _cfg("regime_detection", "adx_sigmoid_k") or 0.25
    trend_strength = _sigmoid(adx - adx_center, k=adx_k)

    # Volatility strength: 0 = calm, 1 = high volatility
    # Centered at BB width = 8%
    bb_center = _cfg("regime_detection", "bb_width_center") or 0.08
    bb_k = _cfg("regime_detection", "bb_width_sigmoid_k") or 60.0
    vol_strength = _sigmoid(bb_width - bb_center, k=bb_k)

    # Trend direction bias: -1 (downtrend) to +1 (uptrend)
    trend_scale = _cfg("regime_detection", "trend_bias_scale") or 10.0
    if ma20 > 0:
        trend_bias = min(1.0, max(-1.0, (current_close / ma20 - 1.0) * trend_scale))
    else:
        trend_bias = 0.0

    # Base weights
    w_tech = _cfg("weights", "dyn_base_technical") or 0.35
    w_sent = _cfg("weights", "dyn_base_sentiment") or 0.20
    w_risk = _cfg("weights", "dyn_base_risk") or 0.25
    w_mom = _cfg("weights", "dyn_base_momentum") or 0.10
    w_fund = _cfg("weights", "dyn_base_fundamental") or 0.10

    # Trending → momentum matters more, slightly less on fundamentals
    t_mom = _cfg("weights", "dyn_trend_mom_boost") or 0.18
    t_fund = _cfg("weights", "dyn_trend_fund_cut") or 0.05
    w_mom += t_mom * trend_strength
    w_fund -= t_fund * trend_strength

    # Volatile → risk + sentiment matter more, momentum less reliable
    v_risk = _cfg("weights", "dyn_vol_risk_boost") or 0.10
    v_sent = _cfg("weights", "dyn_vol_sent_boost") or 0.10
    v_mom = _cfg("weights", "dyn_vol_mom_cut") or 0.08
    w_risk += v_risk * vol_strength
    w_sent += v_sent * vol_strength
    w_mom -= v_mom * vol_strength

    # Downtrend → shift from momentum to sentiment (panic/fear dominate)
    down_thresh = _cfg("regime_detection", "downtrend_threshold") or -0.02
    down_scale = _cfg("weights", "dyn_downtrend_shift_scale") or 0.12
    if trend_bias < down_thresh:
        shift = abs(trend_bias) * down_scale
        w_mom -= shift
        w_sent += shift

    # Uptrend → slight momentum boost
    up_thresh = _cfg("regime_detection", "uptrend_threshold") or 0.02
    up_scale = _cfg("weights", "dyn_uptrend_shift_scale") or 0.08
    up_tech = _cfg("weights", "dyn_uptrend_tech_bonus") or 0.50
    if trend_bias > up_thresh:
        shift = trend_bias * up_scale
        w_mom += shift
        w_tech += shift * up_tech

    # Clamp all weights to valid range
    w_tech = max(_cfg("weights", "dyn_clamp_tech_min") or 0.15, min(_cfg("weights", "dyn_clamp_tech_max") or 0.50, w_tech))
    w_sent = max(_cfg("weights", "dyn_clamp_sent_min") or 0.10, min(_cfg("weights", "dyn_clamp_sent_max") or 0.40, w_sent))
    w_risk = max(_cfg("weights", "dyn_clamp_risk_min") or 0.10, min(_cfg("weights", "dyn_clamp_risk_max") or 0.40, w_risk))
    w_mom = max(_cfg("weights", "dyn_clamp_mom_min") or 0.02, min(_cfg("weights", "dyn_clamp_mom_max") or 0.35, w_mom))
    w_fund = max(_cfg("weights", "dyn_clamp_fund_min") or 0.05, min(_cfg("weights", "dyn_clamp_fund_max") or 0.20, w_fund))

    # Normalize to sum = 1.0
    total = w_tech + w_sent + w_risk + w_mom + w_fund
    result = {
        "technical": round(w_tech / total, 4),
        "sentiment": round(w_sent / total, 4),
        "risk": round(w_risk / total, 4),
        "momentum": round(w_mom / total, 4),
        "fundamental": round(w_fund / total, 4),
    }
    return result


# Legacy regime weights — kept for backward compatibility and as reference
# (now superseded by _compute_dynamic_weights in the main code path)
REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
    "trending_up": {
        "technical": 0.30, "sentiment": 0.15, "risk": 0.20,
        "momentum": 0.25, "fundamental": 0.10,
    },
    "trending_down": {
        "technical": 0.30, "sentiment": 0.35, "risk": 0.20,
        "momentum": 0.05, "fundamental": 0.10,
    },
    "ranging": {
        "technical": 0.45, "sentiment": 0.15, "risk": 0.15,
        "momentum": 0.10, "fundamental": 0.15,
    },
    "volatile": {
        "technical": 0.25, "sentiment": 0.25, "risk": 0.30,
        "momentum": 0.05, "fundamental": 0.15,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def _ensure_fresh_klines(db: AsyncSession, symbol: str, interval: str):
    """Ensure fresh kline data exists.

    - mock mode: regenerate mock klines from current ticker price
    - real provider mode: check for existing klines, fetch if missing or stale
    """
    from app.core.market_constants import MOCK_CONFIG
    from app.core.config import settings
    from app.services.data_provider import mock_market_data, afetch_real_klines
    from app.models.market_data import KLine as KLineModel

    now = datetime.now(timezone.utc)

    # Check current kline state
    result = await db.execute(
        select(KLine.timestamp)
        .where(KLine.symbol == symbol, KLine.interval == interval)
        .order_by(KLine.timestamp.desc())
        .limit(1)
    )
    latest = result.scalar_one_or_none()

    if latest and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)

    fresh_secs = _cfg("data", "klines_fresh_seconds") or 21600
    klines_fresh = latest and (now - latest).total_seconds() < fresh_secs

    # --- Mock mode: regenerate from ticker price ---
    if settings.MARKET_DATA_PROVIDER in ("mock", ""):
        if klines_fresh:
            return

        config = MOCK_CONFIG.get(symbol, {"base_price": 100.0, "days": 90, "interval_minutes": 60})
        days = 120  # Enough for 120 daily bars

        ticker = await get_ticker(db, symbol)
        base_price = float(ticker.last_price) if ticker and ticker.last_price else config["base_price"]

        await db.execute(
            KLineModel.__table__.delete().where(
                KLineModel.symbol == symbol, KLineModel.interval == interval
            )
        )
        await db.flush()

        interval_minutes = 1440  # 1d
        data = mock_market_data(
            base_price=base_price,
            days=days,
            interval_minutes=interval_minutes,
            volatility=0.02,
        )
        await save_kline_data(db, symbol, interval, data)
        return

    # --- Real data provider mode ---
    if klines_fresh:
        return

    # Fetch real klines from configured provider (sina / eastmoney / akshare)
    kline_data = await afetch_real_klines(symbol, interval)
    if kline_data:
        saved = await save_kline_data(db, symbol, interval, kline_data)
        logger.info("为 %s:%s 获取了 %s 条真实K线数据", symbol, interval, saved)
    elif not latest:
        logger.warning(
            "%s:%s 无K线数据 — 真实数据源(%s)获取失败，跳过决策生成",
            symbol, interval, settings.MARKET_DATA_PROVIDER,
        )


async def generate_decision(
    db: AsyncSession,
    user_id: int,
    symbol: str,
) -> Optional[Dict[str, Any]]:
    """
    Generate an investment decision for a single symbol.

    Returns the serialized InvestmentDecision dict, or None if insufficient data.
    """
    user = await db.get(User, user_id)
    if not user:
        return None

    # Ensure fresh kline data for accurate analysis
    await _ensure_fresh_klines(db, symbol, "1d")

    dl_limit = int(_cfg("data", "daily_klines_limit") or 200)
    min_bars = int(_cfg("data", "min_klines_daily") or 20)
    klines = await get_kline_data(db, symbol, "1d", limit=dl_limit)
    ticker = await get_ticker(db, symbol)

    if not klines or len(klines) < min_bars:
        return None

    data = _klines_to_arrays(klines)

    # 优先使用实时ticker价格，仅当ticker不可靠时才用K线收盘价
    kline_close = float(data["close"][-1])
    from app.services.data_authenticity import verify_data_source, REAL_SOURCES

    if ticker and ticker.last_price and ticker.data_source in REAL_SOURCES:
        # 真实ticker数据可用，以实时行情为准
        current_price = float(ticker.last_price)
    elif ticker and ticker.last_price:
        # ticker存在但来源不可靠，用K线收盘价
        current_price = kline_close
        ticker.last_price = current_price
        ticker.updated_at = datetime.now(timezone.utc)
        if klines:
            kline_source = getattr(klines[-1], 'data_source', 'unknown')
            if kline_source in REAL_SOURCES and (not ticker.data_source or ticker.data_source == 'unknown'):
                ticker.data_source = kline_source
        await db.flush()
    else:
        # 无ticker，以K线收盘价为准
        current_price = kline_close

    # Cross-validate ticker price against kline data to catch bad data
    if kline_close > 0:
        price_delta = abs(current_price - kline_close) / kline_close
        if price_delta > 0.10:  # >10% deviation = likely bad data
            logger.warning(
                "数据异常: %s ticker价格 %.2f 与K线收盘 %.2f 偏差 %.0f%%, 回退到K线收盘价",
                symbol, current_price, kline_close, price_delta * 100,
            )
            current_price = kline_close
            if ticker:
                ticker.last_price = kline_close
                await db.flush()

    # 追踪数据来源
    if klines:
        kline_source = getattr(klines[-1], 'data_source', 'unknown')
        if not verify_data_source(kline_source, operation=f"生成{symbol}投资决策"):
            logger.warning(
                "%s 的投资决策基于来源为 '%s' 的数据，可信度可能失真",
                symbol, kline_source,
            )

    # Detect market regime and compute continuous dynamic weights
    regime = _detect_regime(data)
    adx = _calc_adx(
        data["high"], data["low"], data["close"]
    ) if "high" in data and "low" in data else 25.0
    ma20 = float(_rolling_mean(data["close"], 20)[-1]) if len(data["close"]) >= 20 else current_price
    bb_std = float(_rolling_std(data["close"], 20, ddof=0)[-1]) if len(data["close"]) >= 20 else 0
    bb_width = (2.0 * bb_std) / ma20 if not math.isnan(bb_std) and ma20 > 0 else 0.05
    weights = _compute_dynamic_weights(adx, bb_width, current_price, ma20)

    # Multi-factor scoring
    technical_result = _calc_technical_score(data)
    sentiment_result = _calc_sentiment_score(ticker)
    risk_result = await _calc_risk_score(db, user, symbol, current_price)
    momentum_result = _calc_momentum_score(data)

    # Fundamental / ETF tracking factor (P4)
    from app.services import etf_utils
    if etf_utils.is_etf(symbol):
        fundamental_result = etf_utils.fetch_etf_tracking_error(symbol, data)
    else:
        from app.services.data_provider import fetch_fundamental_data
        fund_data = await _afetch_fundamental(db, symbol)
        fundamental_result = _calc_fundamental_score(fund_data)

    # Multi-timeframe: fetch weekly data and compute weekly technical (P2)
    weekly_technical = None
    tech_score_for_composite = technical_result["score"]
    try:
        weekly_data = await _get_weekly_data(db, symbol)
        if weekly_data and len(weekly_data["close"]) >= 20:
            weekly_technical = _calc_technical_score(weekly_data)
    except Exception:
        pass

    # Integrate weekly as a sub-factor within technical (70% daily + 30% weekly)
    # instead of a post-hoc multiplier — this preserves the weighting framework
    if weekly_technical:
        td_w = _cfg("scoring", "technical_daily_weight") or 0.70
        tw_w = _cfg("scoring", "technical_weekly_weight") or 0.30
        tech_score_for_composite = round(
            technical_result["score"] * td_w + weekly_technical["score"] * tw_w, 1
        )

    # Weighted composite (linear combination)
    raw_weighted = (
        tech_score_for_composite * weights["technical"]
        + sentiment_result["score"] * weights["sentiment"]
        + risk_result["score"] * weights["risk"]
        + momentum_result["score"] * weights["momentum"]
        + fundamental_result["score"] * weights["fundamental"]
    )

    # Signal disagreement penalty (P5): penalize contradictory factors
    factor_scores = np.array([
        tech_score_for_composite,
        sentiment_result["score"],
        risk_result["score"],
        momentum_result["score"],
        fundamental_result["score"],
    ])
    score_std = float(np.std(factor_scores))
    # Penalty scales with disagreement magnitude; capped so it doesn't dominate
    pen_scale = _cfg("scoring", "disagreement_penalty_scale") or 0.30
    pen_cap = _cfg("scoring", "disagreement_penalty_cap") or 15.0
    disagreement_penalty = min(score_std * pen_scale, pen_cap)
    composite = round(raw_weighted - disagreement_penalty, 1)
    composite = max(0.0, min(100.0, composite))

    # True confidence: separates signal strength from signal agreement
    # High agreement → confidence boosted; high disagreement → confidence penalized
    af_min = _cfg("scoring", "agreement_factor_min") or 0.40
    af_div = _cfg("scoring", "agreement_factor_divisor") or 80.0
    agreement_factor = max(af_min, 1.0 - score_std / af_div)
    confidence = min(100, max(0, int(composite * agreement_factor)))
    # Floor confidence at 10 when we have real data but extreme disagreement
    cf_thresh = _cfg("scoring", "confidence_floor_threshold") or 10
    cf_value = _cfg("scoring", "confidence_floor_value") or 10
    if confidence < cf_thresh and composite > cf_value * 2:
        confidence = cf_value

    # Regime transition detection: bonus/penalty for inflection points
    regime_transition = _detect_regime_transition(
        data["close"], data["high"], data["low"]
    )
    if regime_transition["transition"] != "steady":
        composite = round(composite * regime_transition["transition_multiplier"], 1)
        composite = max(0.0, min(100.0, composite))

    # Market-relative baseline adjustment: score in context of trend
    market_ctx = _calc_market_context_adjustment(data["close"])
    composite = round(composite + market_ctx["adjustment"], 1)
    composite = max(0.0, min(100.0, composite))

    # Risk-aware recommendation thresholds
    recommendation = _score_to_recommendation(composite, agreement_factor)
    target_price, stop_loss = _calc_target_stop(data, composite)

    factors = {
        "technical_score": technical_result["score"],
        "sentiment_score": sentiment_result["score"],
        "risk_score": risk_result["score"],
        "momentum_score": momentum_result["score"],
        "fundamental_score": fundamental_result["score"],
        "composite_score": composite,
        "raw_weighted": round(raw_weighted, 1),
        "disagreement_penalty": round(disagreement_penalty, 1),
        "agreement_factor": round(agreement_factor, 2),
        "score_std": round(score_std, 1),
        "technical": technical_result,
        "sentiment": sentiment_result,
        "risk": risk_result,
        "momentum": momentum_result,
        "fundamental": fundamental_result,
        "regime": regime,
        "regime_transition": regime_transition["transition"],
        "market_context": market_ctx,
        "weights": weights,
    }
    if weekly_technical:
        factors["weekly_technical"] = weekly_technical
        factors["tech_score_for_composite"] = tech_score_for_composite

    # Extract price date from the latest kline bar, display in CST (UTC+8)
    latest_kline_ts = klines[-1].timestamp if klines else None
    if latest_kline_ts:
        if latest_kline_ts.tzinfo is None:
            latest_kline_ts = latest_kline_ts.replace(tzinfo=timezone.utc)
        cst = timezone(timedelta(hours=8))
        price_date = latest_kline_ts.astimezone(cst).strftime("%Y-%m-%d")
    else:
        price_date = ""

    reasoning = _build_reasoning(symbol, recommendation, composite, factors, current_price, price_date)

    # Expire any existing active decisions for this stock so "当前建议" doesn't duplicate
    from sqlalchemy import update as sa_update
    await db.execute(
        sa_update(InvestmentDecision)
        .where(
            InvestmentDecision.user_id == user_id,
            InvestmentDecision.symbol == symbol,
            InvestmentDecision.status == DecisionStatus.ACTIVE,
        )
        .values(status=DecisionStatus.EXPIRED)
    )

    decision = InvestmentDecision(
        user_id=user_id,
        symbol=symbol,
        recommendation=recommendation,
        confidence=confidence,
        target_price=target_price,
        stop_loss=stop_loss,
        factors=factors,
        reasoning=reasoning,
        status=DecisionStatus.ACTIVE,
        valid_until=datetime.now(timezone.utc) + timedelta(hours=int(_cfg("data", "decision_valid_hours") or 24)),
    )
    db.add(decision)
    await db.flush()
    await db.refresh(decision)

    if recommendation in (DecisionRecommendation.STRONG_BUY, DecisionRecommendation.STRONG_SELL):
        await create_notification(
            db,
            user_id=user_id,
            type="strategy",
            title=f"投资决策: {_recommendation_label(recommendation)} - {symbol}",
            content=reasoning[:500],
        )
        # WebSocket 实时推送
        try:
            from app.services.ws_manager import manager
            await manager.broadcast({
                "type": "decision_alert",
                "data": {
                    "symbol": symbol,
                    "recommendation": recommendation.value,
                    "label": _recommendation_label(recommendation),
                    "confidence": confidence,
                    "price": current_price,
                    "summary": reasoning[:200],
                },
            })
        except Exception:
            pass

    return _decision_to_dict(decision)


async def generate_decisions_batch(
    db: AsyncSession,
    user_id: int,
    symbols: List[str],
) -> List[Dict[str, Any]]:
    """Generate decisions for multiple symbols."""
    results = []
    for symbol in symbols:
        try:
            decision = await generate_decision(db, user_id, symbol)
            if decision:
                results.append(decision)
        except Exception:
            continue
    return results


async def get_decisions(
    db: AsyncSession,
    user_id: int,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated list of decisions for a user."""
    query = select(InvestmentDecision).where(InvestmentDecision.user_id == user_id)

    if status:
        query = query.where(InvestmentDecision.status == status)
    if symbol:
        query = query.where(InvestmentDecision.symbol == symbol)

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(desc(InvestmentDecision.created_at))
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    items = [_decision_to_dict(d) for d in result.scalars().all()]

    return items, total


async def get_decision(db: AsyncSession, decision_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Get a single decision by ID."""
    result = await db.execute(
        select(InvestmentDecision).where(
            InvestmentDecision.id == decision_id,
            InvestmentDecision.user_id == user_id,
        )
    )
    decision = result.scalar_one_or_none()
    return _decision_to_dict(decision) if decision else None


async def get_decision_summary(db: AsyncSession, user_id: int) -> Dict[str, Any]:
    """Aggregate summary for the decision dashboard."""
    result = await db.execute(
        select(InvestmentDecision).where(
            InvestmentDecision.user_id == user_id,
            InvestmentDecision.status == DecisionStatus.ACTIVE,
        ).order_by(desc(InvestmentDecision.created_at))
    )
    actives = list(result.scalars().all())

    total = len(actives)
    counts: Dict[str, int] = {"strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0}
    for d in actives:
        key = d.recommendation.value if hasattr(d.recommendation, "value") else d.recommendation
        if key in counts:
            counts[key] += 1

    avg_conf = round(sum(d.confidence for d in actives) / total, 1) if total > 0 else 0.0

    buys = [d for d in actives if d.recommendation in (DecisionRecommendation.STRONG_BUY, DecisionRecommendation.BUY)]
    buys.sort(key=lambda x: x.confidence, reverse=True)
    top_n = int(_cfg("data", "summary_top_n") or 5)
    recent_n = int(_cfg("data", "summary_recent_n") or 10)
    top_picks = [_decision_to_dict(d) for d in buys[:top_n]]

    recent = [_decision_to_dict(d) for d in actives[:recent_n]]

    return {
        "total_active": total,
        "strong_buy_count": counts["strong_buy"],
        "buy_count": counts["buy"],
        "hold_count": counts["hold"],
        "sell_count": counts["sell"],
        "strong_sell_count": counts["strong_sell"],
        "avg_confidence": avg_conf,
        "top_picks": top_picks,
        "recent_decisions": recent,
    }


async def execute_decision(db: AsyncSession, decision_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Mark a decision as executed."""
    result = await db.execute(
        select(InvestmentDecision).where(
            InvestmentDecision.id == decision_id,
            InvestmentDecision.user_id == user_id,
        )
    )
    decision = result.scalar_one_or_none()
    if not decision:
        return None
    decision.status = DecisionStatus.EXECUTED
    decision.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(decision)
    return _decision_to_dict(decision)


async def dismiss_decision(db: AsyncSession, decision_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """Dismiss a recommendation."""
    result = await db.execute(
        select(InvestmentDecision).where(
            InvestmentDecision.id == decision_id,
            InvestmentDecision.user_id == user_id,
        )
    )
    decision = result.scalar_one_or_none()
    if not decision:
        return None
    decision.status = DecisionStatus.DISMISSED
    decision.updated_at = datetime.now(timezone.utc)
    await db.flush()
    await db.refresh(decision)
    return _decision_to_dict(decision)


async def expire_old_decisions(db: AsyncSession) -> int:
    """Expire active decisions past their valid_until date."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(InvestmentDecision).where(
            InvestmentDecision.status == DecisionStatus.ACTIVE,
            InvestmentDecision.valid_until < now,
        )
    )
    expired = list(result.scalars().all())
    for d in expired:
        d.status = DecisionStatus.EXPIRED
        d.updated_at = now
    await db.flush()
    return len(expired)


async def audit_suspicious_decisions(db: AsyncSession) -> int:
    """Flag decisions whose target price is implausibly far from the latest kline close.

    Returns the number of decisions auto-expired as suspicious.
    """
    from app.models.market_data import KLine

    # Find active decisions
    result = await db.execute(
        select(InvestmentDecision).where(
            InvestmentDecision.status == DecisionStatus.ACTIVE,
        )
    )
    active = list(result.scalars().all())
    expired_count = 0

    for d in active:
        # Get latest kline close for this symbol
        kline_result = await db.execute(
            select(KLine.close)
            .where(KLine.symbol == d.symbol, KLine.interval == "1d")
            .order_by(KLine.timestamp.desc())
            .limit(1)
        )
        latest_close = kline_result.scalar_one_or_none()
        if not latest_close or latest_close <= 0:
            continue

        # If target price is >50% away from last close, the data was likely bad
        if d.target_price and abs(d.target_price - latest_close) / latest_close > 0.50:
            logger.warning(
                "可疑决策 #%d %s: 目标价 %.2f 与最新收盘 %.2f 偏差 %.0f%%, 自动过期",
                d.id, d.symbol, d.target_price, latest_close,
                abs(d.target_price - latest_close) / latest_close * 100,
            )
            d.status = DecisionStatus.EXPIRED
            d.updated_at = datetime.now(timezone.utc)
            expired_count += 1

    if expired_count:
        await db.flush()
        logger.info("审计完成: %d 条活跃决策中自动过期 %d 条可疑记录", len(active), expired_count)

    return expired_count


# ---------------------------------------------------------------------------
# NumPy helper functions (replacing pandas operations)
# ---------------------------------------------------------------------------


def _rolling_mean(arr: np.ndarray, window: int) -> np.ndarray:
    """Simple moving average over a 1D array."""
    if len(arr) < window:
        return np.full_like(arr, np.nan, dtype=float)
    out = np.full_like(arr, np.nan, dtype=float)
    cumsum = np.cumsum(np.insert(arr.astype(float), 0, 0))
    out[window - 1:] = (cumsum[window:] - cumsum[:-window]) / window
    return out


def _rolling_std(arr: np.ndarray, window: int, ddof: int = 0) -> np.ndarray:
    """Rolling standard deviation."""
    if len(arr) < window:
        return np.full_like(arr, np.nan, dtype=float)
    out = np.full_like(arr, np.nan, dtype=float)
    for i in range(window - 1, len(arr)):
        out[i] = np.std(arr[i - window + 1 : i + 1], ddof=ddof)
    return out


def _ewma(arr: np.ndarray, span: int) -> np.ndarray:
    """Exponential weighted moving average with given span (adjust=False)."""
    alpha = 1.0 / span
    out = np.zeros_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """EMA with smoothing factor alpha = 2/(period+1)."""
    alpha = 2.0 / (period + 1)
    out = np.zeros_like(arr, dtype=float)
    out[0] = arr[0]
    for i in range(1, len(arr)):
        out[i] = alpha * arr[i] + (1 - alpha) * out[i - 1]
    return out


def _wilder_ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing: EMA with alpha = 1/period (used for RSI, ADX).

    Differs from standard EMA — alpha = 1/N instead of 2/(N+1),
    producing a slower, more stable smoothing suitable for oscillator baselines.
    """
    alpha = 1.0 / period
    out = np.full_like(arr, np.nan, dtype=float)
    # Seed at first non-zero / non-nan position
    for i in range(len(arr)):
        if not np.isnan(arr[i]):
            out[i] = float(arr[i])
            break
    for i in range(1, len(arr)):
        if not np.isnan(arr[i]) and not np.isnan(out[i - 1]):
            out[i] = alpha * float(arr[i]) + (1 - alpha) * out[i - 1]
        elif not np.isnan(arr[i]):
            out[i] = float(arr[i])
    return out


# ---------------------------------------------------------------------------
# Scoring normalization (P0b)
# ---------------------------------------------------------------------------


def _normalize_score(adjustments: List[float], baseline: float = 50.0) -> float:
    """
    Normalize a list of signal adjustments into a 0-100 score.

    Uses tanh with divisor=70 (wider linear region vs the previous 50) so
    it takes ~40% more signal accumulation before saturating — this preserves
    differentiation in the high-confidence range (80-100) where the old
    scaling would flatten genuinely strong vs record-strength signals together.

    baseline + 50 * tanh(sum / 70) keeps the output in (0, 100) naturally.
    """
    if not adjustments:
        return baseline
    total = sum(adjustments)
    divisor = _cfg("scoring", "tanh_divisor") or 70.0
    normalized = baseline + 50.0 * math.tanh(total / divisor)
    return round(max(0.0, min(100.0, normalized)), 1)


# ---------------------------------------------------------------------------
# KDJ calculation (P0c) — pure numpy, no pandas
# ---------------------------------------------------------------------------


def _calc_kdj(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              n: int = 9, k_period: int = 3, d_period: int = 3) -> Dict[str, Any]:
    """
    Calculate KDJ indicator values and crossover signals.

    Returns dict with k, d, j values, golden_cross (K crosses above D),
    death_cross (K crosses below D), and overbought/oversold flags.
    """
    length = len(close)
    if length < n + max(k_period, d_period) + 1:
        return {"k": 50.0, "d": 50.0, "j": 50.0, "golden_cross": False,
                "death_cross": False, "overbought": False, "oversold": False}

    k_values = np.full(length, np.nan)
    d_values = np.full(length, np.nan)

    for i in range(n - 1, length):
        high_n = float(np.max(high[i - n + 1 : i + 1]))
        low_n = float(np.min(low[i - n + 1 : i + 1]))
        if high_n != low_n:
            rsv = (float(close[i]) - low_n) / (high_n - low_n) * 100.0
        else:
            rsv = 50.0
        if i == n - 1:
            k_values[i] = rsv
            d_values[i] = rsv
        else:
            k_values[i] = (k_period - 1) / k_period * k_values[i - 1] + (1.0 / k_period) * rsv
            d_values[i] = (d_period - 1) / d_period * d_values[i - 1] + (1.0 / d_period) * k_values[i]

    k_now = float(k_values[-1])
    d_now = float(d_values[-1])
    j_now = 3.0 * k_now - 2.0 * d_now

    k_prev = float(k_values[-2]) if length >= 2 else k_now
    d_prev = float(d_values[-2]) if length >= 2 else d_now

    golden_cross = k_prev <= d_prev and k_now > d_now
    death_cross = k_prev >= d_prev and k_now < d_now

    return {
        "k": round(k_now, 2),
        "d": round(d_now, 2),
        "j": round(j_now, 2),
        "golden_cross": golden_cross,
        "death_cross": death_cross,
        "overbought": j_now > 100,
        "oversold": j_now < 0,
    }


# ---------------------------------------------------------------------------
# Volume divergence detection (P0d)
# ---------------------------------------------------------------------------


def _detect_volume_divergence(data: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """
    Detect volume-price divergence.

    Bullish divergence: price makes lower low but volume is also declining
    Bearish divergence: price makes higher high but volume is declining
    """
    close = data["close"]
    vol = data.get("volume")
    result: Dict[str, Any] = {"bullish_divergence": False, "bearish_divergence": False,
                               "signals": []}

    if vol is None or len(close) < 20:
        return result

    n = len(close)
    # Look at two recent segments: [-10:-5] and [-5:]
    mid = n - 5

    # Compare price highs
    prev_high = float(np.max(close[mid - 5 : mid]))
    curr_high = float(np.max(close[mid:]))
    prev_vol_high = float(np.mean(vol[mid - 5 : mid]))
    curr_vol_high = float(np.mean(vol[mid:]))

    # Compare price lows
    prev_low = float(np.min(close[mid - 5 : mid]))
    curr_low = float(np.min(close[mid:]))
    prev_vol_low = float(np.mean(vol[mid - 5 : mid]))
    curr_vol_low = float(np.mean(vol[mid:]))

    # Bearish: price higher high, volume lower → distribution/weakness
    if curr_high > prev_high * 1.01 and curr_vol_high < prev_vol_high * 0.85:
        result["bearish_divergence"] = True
        result["signals"].append("量价顶背离(价格新高但量能不足)")

    # Bullish: price lower low, volume higher → accumulation/strength
    if curr_low < prev_low * 0.99 and curr_vol_low > prev_vol_low * 1.15:
        result["bullish_divergence"] = True
        result["signals"].append("量价底背离(价格新低但量能放大)")

    return result


# ---------------------------------------------------------------------------
# MACD divergence detection
# ---------------------------------------------------------------------------


def _detect_macd_divergence(close: np.ndarray, high: np.ndarray, low: np.ndarray, n: int = 30) -> Dict[str, Any]:
    """
    Detect MACD-price divergence — the most informative MACD signal.

    Bullish divergence: price makes lower low but MACD histogram makes higher low
    Bearish divergence: price makes higher high but MACD histogram makes lower high

    Uses peak/trough detection over a lookback window.
    """
    result: Dict[str, Any] = {"bullish_divergence": False, "bearish_divergence": False,
                               "signals": []}
    if n < 40:
        return result

    ema_fast = _ema(close, 12)
    ema_slow = _ema(close, 26)
    signal_line = _ema(ema_fast - ema_slow, 9)
    hist = ema_fast - ema_slow - signal_line

    # Focus on the last 20 bars for recent divergence
    lookback = min(25, n - 5)
    recent_close = close[-lookback:]
    recent_hist = hist[-lookback:]

    # Find peaks and troughs in price and MACD histogram
    price_peaks = []
    price_troughs = []
    macd_peaks = []
    macd_troughs = []

    for i in range(2, len(recent_close) - 2):
        # Price peaks (local maxima)
        if recent_close[i] > recent_close[i - 1] and recent_close[i] > recent_close[i - 2] \
           and recent_close[i] > recent_close[i + 1] and recent_close[i] > recent_close[i + 2]:
            price_peaks.append((i, float(recent_close[i]), float(recent_hist[i])))
        # Price troughs (local minima)
        if recent_close[i] < recent_close[i - 1] and recent_close[i] < recent_close[i - 2] \
           and recent_close[i] < recent_close[i + 1] and recent_close[i] < recent_close[i + 2]:
            price_troughs.append((i, float(recent_close[i]), float(recent_hist[i])))
        # MACD histogram peaks
        if recent_hist[i] > recent_hist[i - 1] and recent_hist[i] > recent_hist[i - 2] \
           and recent_hist[i] > recent_hist[i + 1] and recent_hist[i] > recent_hist[i + 2]:
            macd_peaks.append((i, float(recent_close[i]), float(recent_hist[i])))
        # MACD histogram troughs
        if recent_hist[i] < recent_hist[i - 1] and recent_hist[i] < recent_hist[i - 2] \
           and recent_hist[i] < recent_hist[i + 1] and recent_hist[i] < recent_hist[i + 2]:
            macd_troughs.append((i, float(recent_close[i]), float(recent_hist[i])))

    # Check last two peaks for bearish divergence
    if len(price_peaks) >= 2 and len(macd_peaks) >= 2:
        p1, p2 = price_peaks[-2], price_peaks[-1]
        m1, m2 = macd_peaks[-2], macd_peaks[-1]
        # Price higher high, MACD histogram lower high
        if p2[1] > p1[1] and m2[2] < m1[2]:
            result["bearish_divergence"] = True
            result["signals"].append("MACD顶背离(价格新高但动能减弱)")

    # Check last two troughs for bullish divergence
    if len(price_troughs) >= 2 and len(macd_troughs) >= 2:
        p1, p2 = price_troughs[-2], price_troughs[-1]
        m1, m2 = macd_troughs[-2], macd_troughs[-1]
        # Price lower low, MACD histogram higher low
        if p2[1] < p1[1] and m2[2] > m1[2]:
            result["bullish_divergence"] = True
            result["signals"].append("MACD底背离(价格新低但动能回升)")

    return result


# ---------------------------------------------------------------------------
# RSI divergence detection
# ---------------------------------------------------------------------------


def _detect_rsi_divergence(close: np.ndarray, period: int = 14, n: int = 30) -> Dict[str, Any]:
    """
    Detect RSI-price divergence — independent confirmation of MACD divergence.

    Bullish divergence: price makes lower low but RSI makes higher low
    Bearish divergence: price makes higher high but RSI makes lower high

    Uses peak/trough detection over a lookback window.
    """
    result: Dict[str, Any] = {"bullish_divergence": False, "bearish_divergence": False,
                               "signals": []}
    if n < 40:
        return result

    # Compute RSI over the full array
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = _wilder_ema(gain, period)
    avg_loss = _wilder_ema(loss, period)
    rsi = np.full_like(close, 50.0, dtype=float)
    for i in range(len(close)):
        if not np.isnan(avg_gain[i]) and not np.isnan(avg_loss[i]):
            if avg_loss[i] == 0:
                rsi[i] = 100.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    # Focus on the last 25 bars
    lookback = min(25, n - 5)
    recent_close = close[-lookback:]
    recent_rsi = rsi[-lookback:]

    # Find peaks and troughs in price and RSI
    price_peaks: list = []
    price_troughs: list = []
    rsi_peaks: list = []
    rsi_troughs: list = []

    for i in range(2, len(recent_close) - 2):
        # Price peaks
        if (recent_close[i] > recent_close[i - 1] and recent_close[i] > recent_close[i - 2]
                and recent_close[i] > recent_close[i + 1] and recent_close[i] > recent_close[i + 2]):
            price_peaks.append((i, float(recent_close[i]), float(recent_rsi[i])))
        # Price troughs
        if (recent_close[i] < recent_close[i - 1] and recent_close[i] < recent_close[i - 2]
                and recent_close[i] < recent_close[i + 1] and recent_close[i] < recent_close[i + 2]):
            price_troughs.append((i, float(recent_close[i]), float(recent_rsi[i])))
        # RSI peaks
        if (recent_rsi[i] > recent_rsi[i - 1] and recent_rsi[i] > recent_rsi[i - 2]
                and recent_rsi[i] > recent_rsi[i + 1] and recent_rsi[i] > recent_rsi[i + 2]):
            rsi_peaks.append((i, float(recent_close[i]), float(recent_rsi[i])))
        # RSI troughs
        if (recent_rsi[i] < recent_rsi[i - 1] and recent_rsi[i] < recent_rsi[i - 2]
                and recent_rsi[i] < recent_rsi[i + 1] and recent_rsi[i] < recent_rsi[i + 2]):
            rsi_troughs.append((i, float(recent_close[i]), float(recent_rsi[i])))

    # Bearish divergence: price higher high, RSI lower high
    if len(price_peaks) >= 2 and len(rsi_peaks) >= 2:
        p1, p2 = price_peaks[-2], price_peaks[-1]
        r1, r2 = rsi_peaks[-2], rsi_peaks[-1]
        if p2[1] > p1[1] and r2[2] < r1[2]:
            result["bearish_divergence"] = True
            result["signals"].append("RSI顶背离(价格新高但RSI动能减弱)")

    # Bullish divergence: price lower low, RSI higher low
    if len(price_troughs) >= 2 and len(rsi_troughs) >= 2:
        p1, p2 = price_troughs[-2], price_troughs[-1]
        r1, r2 = rsi_troughs[-2], rsi_troughs[-1]
        if p2[1] < p1[1] and r2[2] > r1[2]:
            result["bullish_divergence"] = True
            result["signals"].append("RSI底背离(价格新低但RSI动能回升)")

    return result


# ---------------------------------------------------------------------------
# Money Flow indicator (Chaikin Money Flow simplified)
# ---------------------------------------------------------------------------


def _calc_money_flow(data: Dict[str, np.ndarray], period: int = 20) -> Dict[str, Any]:
    """
    Calculate simplified Chaikin Money Flow (CMF).

    Measures buying/selling pressure by weighting volume with price position
    within the day's range. CMF > 0 indicates accumulation; CMF < 0 distribution.

    Returns dict with cmf value, trend, and signal flags.
    """
    result: Dict[str, Any] = {"cmf": 0.0, "trend": "neutral", "strong_buying": False,
                               "strong_selling": False, "signals": []}

    high = data.get("high")
    low = data.get("low")
    close = data["close"]
    vol = data.get("volume")

    if high is None or low is None or vol is None or len(close) < period:
        return result

    n = len(close)
    money_flow_multiplier = np.zeros(n)
    money_flow_volume = np.zeros(n)

    for i in range(n):
        h, l = float(high[i]), float(low[i])
        if h != l:
            money_flow_multiplier[i] = ((float(close[i]) - l) - (h - float(close[i]))) / (h - l)
        money_flow_volume[i] = money_flow_multiplier[i] * float(vol[i])

    # CMF over the period
    mf_vol_sum = np.convolve(money_flow_volume, np.ones(period), mode='valid')
    vol_sum = np.convolve(vol.astype(float), np.ones(period), mode='valid')

    if len(mf_vol_sum) > 0 and vol_sum[-1] > 0:
        cmf = float(mf_vol_sum[-1] / vol_sum[-1])
    else:
        cmf = 0.0

    result["cmf"] = round(cmf, 4)

    if cmf > 0.15:
        result["trend"] = "accumulation"
        result["strong_buying"] = True
        result["signals"].append(f"CMF资金流指标={cmf:.3f}(显著流入)")
    elif cmf > 0.05:
        result["trend"] = "mild_accumulation"
        result["signals"].append(f"CMF资金流指标={cmf:.3f}(温和流入)")
    elif cmf < -0.15:
        result["trend"] = "distribution"
        result["strong_selling"] = True
        result["signals"].append(f"CMF资金流指标={cmf:.3f}(显著流出)")
    elif cmf < -0.05:
        result["trend"] = "mild_distribution"
        result["signals"].append(f"CMF资金流指标={cmf:.3f}(温和流出)")

    return result


# ---------------------------------------------------------------------------
# ADX and market regime detection (P1)
# ---------------------------------------------------------------------------


def _calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              period: int = 14) -> float:
    """Calculate Average Directional Index (ADX)."""
    n = len(close)
    if n < period + 1:
        return 20.0  # Default neutral value

    tr = np.zeros(n)
    plus_dm = np.zeros(n)
    minus_dm = np.zeros(n)

    for i in range(1, n):
        h, l = float(high[i]), float(low[i])
        prev_h, prev_l = float(high[i - 1]), float(low[i - 1])
        prev_c = float(close[i - 1])

        tr[i] = max(h - l, abs(h - prev_c), abs(l - prev_c))

        up_move = h - prev_h
        down_move = prev_l - l
        if up_move > down_move and up_move > 0:
            plus_dm[i] = up_move
        if down_move > up_move and down_move > 0:
            minus_dm[i] = down_move

    # Smooth TR and DMs with Wilder's EMA
    atr_arr = _wilder_smooth(tr, period)
    plus_di = 100.0 * _wilder_smooth(plus_dm, period) / np.where(atr_arr > 0, atr_arr, 1)
    minus_di = 100.0 * _wilder_smooth(minus_dm, period) / np.where(atr_arr > 0, atr_arr, 1)

    denom = plus_di + minus_di
    with np.errstate(divide='ignore', invalid='ignore'):
        raw_dx = 100.0 * np.abs(plus_di - minus_di) / np.where(denom > 0, denom, np.nan)
    dx = np.nan_to_num(raw_dx, nan=0.0)
    adx = _wilder_smooth(dx, period)

    return float(adx[-1]) if not math.isnan(adx[-1]) else 20.0


def _wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing: first value is SMA, then EMA with alpha=1/period."""
    out = np.zeros_like(arr, dtype=float)
    n = len(arr)
    start_idx = 0
    for i in range(n):
        if arr[i] != 0 or i > period:
            start_idx = i
            break
    # First non-zero as SMA over next 'period' values, or simple cumulative
    init_slice = arr[start_idx:start_idx + period]
    out[start_idx + period - 1] = float(np.mean(init_slice))
    for i in range(start_idx + period, n):
        out[i] = (arr[i] + (period - 1) * out[i - 1]) / period
    return out


def _detect_regime(data: Dict[str, np.ndarray]) -> str:
    """
    Classify market regime using ADX and Bollinger Band width.

    Returns one of: trending_up, trending_down, ranging, volatile
    """
    close = data["close"]
    high = data["high"]
    low = data["low"]
    n = len(close)

    if n < 30:
        return "ranging"  # Safe default with limited data

    adx = _calc_adx(high, low, close)
    ma20 = float(_rolling_mean(close, 20)[-1])
    current_close = float(close[-1])

    # Bollinger width for volatility assessment
    bb_mid = ma20
    bb_std = float(_rolling_std(close, 20, ddof=0)[-1])
    if not math.isnan(bb_std) and bb_mid > 0:
        bb_width = (2.0 * bb_std) / bb_mid
    else:
        bb_width = 0.05

    if adx >= 25:
        if current_close > ma20:
            return "trending_up"
        return "trending_down"
    else:
        if bb_width > 0.10:
            return "volatile"
        return "ranging"


# ---------------------------------------------------------------------------
# Weekly data & multi-timeframe confirmation (P2)
# ---------------------------------------------------------------------------


def _resample_klines_to_weekly(klines: List[KLine]) -> Optional[Dict[str, np.ndarray]]:
    """
    Resample daily KLine objects into weekly OHLCV arrays using numpy.

    Groups 5 trading days per bar. Returns None if insufficient data.
    """
    if not klines or len(klines) < 25:  # Need at least 5 weeks
        return None

    # Sort by timestamp ascending
    sorted_kl = sorted(klines, key=lambda k: k.timestamp)

    weekly_open = []
    weekly_high = []
    weekly_low = []
    weekly_close = []
    weekly_volume = []

    i = 0
    while i < len(sorted_kl):
        group = sorted_kl[i:i + 5]
        if len(group) < 3:  # Skip partial weeks
            break
        closes = [float(k.close) for k in group]
        highs = [float(k.high) for k in group]
        lows = [float(k.low) for k in group]
        volumes = [float(k.volume or 0) for k in group]

        weekly_open.append(float(group[0].open))
        weekly_high.append(max(highs))
        weekly_low.append(min(lows))
        weekly_close.append(closes[-1])
        weekly_volume.append(sum(volumes))
        i += 5

    if len(weekly_close) < 10:
        return None

    return {
        "open": np.array(weekly_open, dtype=float),
        "high": np.array(weekly_high, dtype=float),
        "low": np.array(weekly_low, dtype=float),
        "close": np.array(weekly_close, dtype=float),
        "volume": np.array(weekly_volume, dtype=float),
    }


async def _get_weekly_data(db: AsyncSession, symbol: str) -> Optional[Dict[str, np.ndarray]]:
    """Fetch weekly kline data, with daily→weekly resampling fallback."""
    # Try direct weekly interval first
    weekly_kl = await get_kline_data(db, symbol, "1w", limit=60)
    if weekly_kl and len(weekly_kl) >= 10:
        return _klines_to_arrays(weekly_kl)

    # Fallback: resample from daily
    wl_limit = int(_cfg("data", "weekly_klines_limit") or 260)
    daily_kl = await get_kline_data(db, symbol, "1d", limit=wl_limit)
    if daily_kl and len(daily_kl) >= 25:
        return _resample_klines_to_weekly(daily_kl)

    return None


def _get_weekly_data_sync(db, symbol: str) -> Optional[Dict[str, np.ndarray]]:
    """Synchronous version for Celery tasks."""
    from sqlalchemy import select as sync_select
    stmt = sync_select(KLine).where(
        KLine.symbol == symbol, KLine.interval == "1d"
    ).order_by(KLine.timestamp.desc()).limit(260)
    daily_kl = list(db.execute(stmt).scalars().all())
    daily_kl.reverse()  # ascending order for chronological resampling
    if daily_kl and len(daily_kl) >= 25:
        return _resample_klines_to_weekly(daily_kl)
    return None


def _apply_weekly_confirmation(composite: float,
                                weekly_tech: Dict[str, Any]) -> float:
    """Apply confirmation/discount based on weekly technical score."""
    from app.models.decision import DecisionRecommendation

    daily_rec = _score_to_recommendation(composite)
    weekly_rec = _score_to_recommendation(weekly_tech["score"])

    buy_signals = (DecisionRecommendation.STRONG_BUY, DecisionRecommendation.BUY)
    sell_signals = (DecisionRecommendation.STRONG_SELL, DecisionRecommendation.SELL)

    if daily_rec in buy_signals and weekly_rec in sell_signals:
        composite *= 0.80  # Strong divergence — discount heavily
    elif daily_rec in sell_signals and weekly_rec in buy_signals:
        composite *= 0.85
    elif daily_rec == weekly_rec:
        composite = min(100.0, composite * 1.05)  # Confirmation boost

    return round(composite, 1)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def _apply_correlation_discount(adjustments: List[float], signals: List[str]) -> List[float]:
    """
    Discount adjustments when correlated indicator groups fire multiple signals.

    Groups that share the same information source:
      oscillator — KDJ + RSI (both measure overbought/oversold cycles)
      trend      — MA cross + MACD histogram direction + multi-MA alignment
      volume     — volume divergence + money flow + volume ratio

    Strategy: count signals per group. If a group has ≥2 active signals,
    discount the total adjustment contribution from that group by scaling
    down non-leading signals. This prevents correlated oscillators from
    dominating the composite when they're all saying the same thing.
    """
    if not adjustments:
        return adjustments

    n = len(adjustments)
    result = list(adjustments)

    # Classify each signal into a group (or None)
    def _group_of(sig_text: str) -> Optional[str]:
        s = sig_text.lower() if isinstance(sig_text, str) else ""
        if any(kw in s for kw in ["kdj", "rsi"]):
            return "oscillator"
        if any(kw in s for kw in ["均线", "macd", "排列"]):
            return "trend"
        if any(kw in s for kw in ["量价", "背离", "cmf", "资金流", "放量", "缩量"]):
            return "volume"
        return None

    # Ensure signals list matches adjustments length (signals may be longer
    # if some are informational-only without adjustments)
    m = min(n, len(signals))

    # Group indices
    groups: Dict[str, List[int]] = {}
    for i in range(m):
        g = _group_of(signals[i])
        if g:
            groups.setdefault(g, []).append(i)

    # For each group with multiple signals, keep the strongest at full weight
    # and discount the rest to 50%
    for group, indices in groups.items():
        if len(indices) < 2:
            continue
        max_idx = max(indices, key=lambda i: abs(result[i]))
        for idx in indices:
            if idx != max_idx:
                result[idx] *= 0.5

    return result


def _calc_technical_score(data: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """Compute technical analysis score (0-100) from multiple indicators."""
    details: Dict[str, Any] = {}
    signals: List[str] = []
    adjustments: List[float] = []

    close = data["close"]
    high = data.get("high")
    low = data.get("low")
    n = len(close)

    # MA trend (5 vs 20)
    ma5_arr = _rolling_mean(close, 5)
    ma20_arr = _rolling_mean(close, 20)
    ma5 = ma5_arr[-1]
    ma20 = ma20_arr[-1]
    if not math.isnan(ma5) and not math.isnan(ma20):
        if ma5 > ma20:
            adjustments.append(10)
            signals.append("短期均线在长期均线上方(多头排列)")
            details["ma_trend"] = "bullish"
        else:
            adjustments.append(-10)
            signals.append("短期均线在长期均线下方(空头排列)")
            details["ma_trend"] = "bearish"
        details["ma5"] = round(ma5, 2)
        details["ma20"] = round(ma20, 2)

    # MACD histogram direction
    if n >= 35:
        ema_fast = _ema(close, 12)
        ema_slow = _ema(close, 26)
        macd_line = ema_fast - ema_slow
        signal_line = _ema(macd_line, 9)
        hist = macd_line - signal_line
        hist_now = float(hist[-1])
        hist_prev = float(hist[-2])
        details["macd_hist"] = round(hist_now, 4)
        if hist_now > 0 and hist_now > hist_prev:
            adjustments.append(8)
            signals.append("MACD柱状图为正且扩大(动能增强)")
        elif hist_now > 0:
            adjustments.append(4)
            signals.append("MACD柱状图为正(多头动能)")
        elif hist_now < 0 and hist_now < hist_prev:
            adjustments.append(-8)
            signals.append("MACD柱状图为负且扩大(动能减弱)")
        elif hist_now < 0:
            adjustments.append(-4)
            signals.append("MACD柱状图为负(空头动能)")

        # MACD-price divergence (high-reliability signal)
        if high is not None and low is not None:
            macd_div = _detect_macd_divergence(close, high, low, n)
            details["macd_divergence"] = macd_div
            if macd_div["bullish_divergence"]:
                adjustments.append(14)
            if macd_div["bearish_divergence"]:
                adjustments.append(-14)
            for sig in macd_div["signals"]:
                signals.append(sig)

    # RSI (14) — Wilder's smoothing per the original Welles Wilder formulation
    if n >= 15:
        delta = np.diff(close, prepend=close[0])
        gain = np.where(delta > 0, delta, 0)
        loss = np.where(delta < 0, -delta, 0)
        avg_gain = _wilder_ema(gain, 14)[-1]
        avg_loss = _wilder_ema(loss, 14)[-1]
        if not np.isnan(avg_gain) and not np.isnan(avg_loss):
            if avg_loss == 0:
                rsi = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi = 100.0 - (100.0 / (1.0 + rs))
            details["rsi"] = round(rsi, 1)
            if rsi < 25:
                adjustments.append(12)
                signals.append(f"RSI={rsi:.0f} 深度超卖(高概率反弹)")
            elif rsi < 35:
                adjustments.append(8)
                signals.append(f"RSI={rsi:.0f} 超卖区域(可能反弹)")
            elif rsi > 75:
                adjustments.append(-12)
                signals.append(f"RSI={rsi:.0f} 深度超买(高概率回调)")
            elif rsi > 65:
                adjustments.append(-8)
                signals.append(f"RSI={rsi:.0f} 超买区域(可能回调)")
            else:
                adjustments.append(2)
                signals.append(f"RSI={rsi:.0f} 中性区间")
            if rsi > 50:
                details["rsi_trend"] = "bullish"
                adjustments.append(2)
            else:
                details["rsi_trend"] = "bearish"
                adjustments.append(-2)

            # RSI-price divergence (independent confirmation of MACD divergence)
            rsi_div = _detect_rsi_divergence(close, 14, n)
            details["rsi_divergence"] = rsi_div
            if rsi_div["bullish_divergence"]:
                adjustments.append(14)
                signals.append("RSI底背离(价格新低但RSI动能回升)")
            if rsi_div["bearish_divergence"]:
                adjustments.append(-14)
                signals.append("RSI顶背离(价格新高但RSI动能减弱)")
        else:
            details["rsi"] = 50.0

    # Bollinger position
    if n >= 20:
        bb_mid = _rolling_mean(close, 20)[-1]
        bb_std = _rolling_std(close, 20, ddof=0)[-1]
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        last_close = float(close[-1])
        if bb_upper != bb_lower:
            details["bb_position"] = round(float((last_close - bb_lower) / (bb_upper - bb_lower) * 100), 1)
        else:
            details["bb_position"] = 50
        if not math.isnan(bb_lower) and last_close <= bb_lower * 1.02:
            adjustments.append(8)
            signals.append("价格接近布林带下轨(超卖)")
        elif not math.isnan(bb_upper) and last_close >= bb_upper * 0.98:
            adjustments.append(-8)
            signals.append("价格接近布林带上轨(超买)")
        details["bb_lower"] = round(float(bb_lower), 2)
        details["bb_upper"] = round(float(bb_upper), 2)

    # KDJ signal (P0c)
    if high is not None and low is not None and n >= 15:
        kdj = _calc_kdj(high, low, close)
        details["kdj_k"] = kdj["k"]
        details["kdj_d"] = kdj["d"]
        details["kdj_j"] = kdj["j"]
        if kdj["golden_cross"] and kdj["k"] < 20:
            adjustments.append(8)
            signals.append(f"KDJ金叉(K={kdj['k']:.1f}, 超卖区域)")
        elif kdj["death_cross"] and kdj["k"] > 80:
            adjustments.append(-8)
            signals.append(f"KDJ死叉(K={kdj['k']:.1f}, 超买区域)")
        elif kdj["golden_cross"]:
            adjustments.append(4)
            signals.append(f"KDJ金叉(K={kdj['k']:.1f})")
        elif kdj["death_cross"]:
            adjustments.append(-4)
            signals.append(f"KDJ死叉(K={kdj['k']:.1f})")
        if kdj["oversold"]:
            signals.append("KDJ进入超卖区")
        elif kdj["overbought"]:
            signals.append("KDJ进入超买区")

    # Volume divergence (P0d) — high-reliability signal
    vol = data.get("volume")
    if vol is not None and n >= 20:
        divergence = _detect_volume_divergence(data)
        details["volume_divergence"] = divergence
        if divergence["bullish_divergence"]:
            adjustments.append(12)
        if divergence["bearish_divergence"]:
            adjustments.append(-12)
        for sig in divergence["signals"]:
            signals.append(sig)

    # Volume trend
    if vol is not None and n >= 6:
        vol_short = np.mean(vol[-5:])
        vol_long = np.mean(vol[-20:]) if n >= 20 else np.mean(vol)
        if vol_long > 0:
            vol_ratio = float(vol_short / vol_long)
            details["volume_ratio"] = round(vol_ratio, 2)
            if vol_ratio > 1.3:
                if details.get("ma_trend") == "bullish":
                    adjustments.append(6)
                    signals.append("放量上涨(资金流入确认)")
                else:
                    adjustments.append(-4)
                    signals.append("放量但趋势偏弱(谨慎)")
            elif vol_ratio < 0.7:
                adjustments.append(-3)
                signals.append("缩量(市场参与度低)")

    # Money flow (Chaikin Money Flow)
    if high is not None and low is not None and vol is not None and n >= 20:
        mf_result = _calc_money_flow(data)
        details["money_flow"] = mf_result
        if mf_result["strong_buying"]:
            adjustments.append(10)
        elif mf_result["strong_selling"]:
            adjustments.append(-10)
        elif mf_result["trend"] == "mild_accumulation":
            adjustments.append(4)
        elif mf_result["trend"] == "mild_distribution":
            adjustments.append(-4)
        for sig in mf_result["signals"]:
            signals.append(sig)

    # Apply signal correlation penalty before normalization
    # Correlated signals share information → discount duplicates within same group
    adjustments = _apply_correlation_discount(adjustments, signals)

    score = _normalize_score(adjustments)
    details["signals"] = signals

    return {
        "score": score,
        "weight": 0.40,
        "label": "技术面分析",
        "details": details,
    }


def _calc_sentiment_score(ticker: Optional[Ticker]) -> Dict[str, Any]:
    """Compute market sentiment score (0-100) from ticker data."""
    details: Dict[str, Any] = {}
    signals: List[str] = []
    adjustments: List[float] = []

    if ticker is None:
        return {"score": 50.0, "weight": 0.20, "label": "市场情绪",
                "details": {"signals": ["无实时行情数据"]}}

    # Log if sentiment is based on non-real data
    data_source = getattr(ticker, 'data_source', 'unknown')
    from app.services.data_authenticity import REAL_SOURCES
    if data_source not in REAL_SOURCES:
        details["data_source_warning"] = f"sentiment基于{data_source}数据,非实时行情"

    change_24h = float(ticker.change_24h or 0)
    last_price = float(ticker.last_price or 0)
    details["change_24h"] = round(change_24h, 2)
    details["last_price"] = round(last_price, 2)

    if change_24h > 5:
        adjustments.append(15)
        signals.append(f"24h涨幅{change_24h:.1f}%(强势)")
    elif change_24h > 2:
        adjustments.append(8)
        signals.append(f"24h涨幅{change_24h:.1f}%(偏强)")
    elif change_24h > 0:
        adjustments.append(3)
        signals.append(f"24h微涨{change_24h:.1f}%")
    elif change_24h > -2:
        adjustments.append(-3)
        signals.append(f"24h微跌{change_24h:.1f}%")
    elif change_24h > -5:
        adjustments.append(-8)
        signals.append(f"24h跌幅{change_24h:.1f}%(偏弱)")
    else:
        adjustments.append(-15)
        signals.append(f"24h跌幅{change_24h:.1f}%(弱势)")

    bid = float(ticker.bid or 0)
    ask = float(ticker.ask or 0)
    if bid > 0 and ask > 0 and last_price > 0:
        spread_pct = (ask - bid) / last_price * 100
        details["spread_pct"] = round(spread_pct, 4)
        details["bid"] = round(bid, 2)
        details["ask"] = round(ask, 2)

        bid_vol = float(ticker.bid_volume or 0)
        ask_vol = float(ticker.ask_volume or 0)
        if bid_vol + ask_vol > 0:
            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
            details["order_imbalance"] = round(float(imbalance), 3)
            if imbalance > 0.2:
                adjustments.append(6)
                signals.append("买方挂单多于卖方(买盘偏强)")
            elif imbalance < -0.2:
                adjustments.append(-6)
                signals.append("卖方挂单多于买方(卖盘偏强)")

    vol_24h = float(ticker.volume_24h or 0)
    turnover = float(ticker.turnover_24h or 0)
    details["volume_24h"] = round(vol_24h, 2)
    details["turnover_24h"] = round(turnover, 2)

    score = _normalize_score(adjustments)
    details["signals"] = signals

    return {
        "score": score,
        "weight": 0.25,
        "label": "市场情绪",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Risk score: pure computation core (P0a — extracted from duplicate)
# ---------------------------------------------------------------------------


def _compute_risk_score(
    positions: List[Any],
    user_max_position_ratio: float,
    user_max_daily_loss: float,
    symbol: str,
    current_price: float,
    risk_event_count: int,
    recent_klines: List[Any],
) -> Dict[str, Any]:
    """Pure function: compute risk score from pre-fetched data."""
    details: Dict[str, Any] = {}
    signals: List[str] = []
    adjustments: List[float] = []

    total_market_value = sum(float(p.market_value or 0) for p in positions)
    symbol_position = next((p for p in positions if p.symbol == symbol), None)

    if symbol_position:
        pos_value = float(symbol_position.market_value or 0)
        pos_ratio = (pos_value / total_market_value * 100) if total_market_value > 0 else 0
        pnl_ratio = float(symbol_position.pnl_ratio or 0)
        details["position_value"] = round(pos_value, 2)
        details["position_ratio"] = round(pos_ratio, 1)
        details["pnl_ratio"] = round(pnl_ratio, 2)

        max_ratio = float(user_max_position_ratio or 30)
        if pos_ratio > max_ratio:
            adjustments.append(-20)
            signals.append(f"持仓占比{pos_ratio:.0f}%超过上限{max_ratio:.0f}%(风险偏高)")
        elif pos_ratio > max_ratio * 0.7:
            adjustments.append(-8)
            signals.append(f"持仓占比{pos_ratio:.0f}%接近上限{max_ratio:.0f}%")

        if pnl_ratio < -5:
            adjustments.append(-12)
            signals.append(f"浮动亏损{pnl_ratio:.1f}%(回撤较大)")
        elif pnl_ratio < -2:
            adjustments.append(-5)
            signals.append(f"浮动亏损{pnl_ratio:.1f}%")
        elif pnl_ratio > 5:
            adjustments.append(5)
            signals.append(f"浮动盈利{pnl_ratio:.1f}%(持仓盈利)")
    else:
        details["position_value"] = 0
        details["position_ratio"] = 0
        details["pnl_ratio"] = 0
        signals.append("无该股票持仓")

    details["recent_risk_events"] = risk_event_count
    if risk_event_count > 0:
        adjustments.append(-risk_event_count * 10)
        signals.append(f"最近24h触发{risk_event_count}次风控事件")

    today_pnl = sum(float(p.day_pnl or 0) for p in positions)
    details["today_pnl"] = round(today_pnl, 2)
    max_daily_loss = float(user_max_daily_loss or 5)
    if max_daily_loss > 0 and today_pnl < 0:
        loss_ratio = abs(today_pnl) / (total_market_value or 1) * 100
        if loss_ratio > max_daily_loss:
            adjustments.append(-25)
            signals.append(f"今日亏损{loss_ratio:.1f}%超过上限{max_daily_loss:.1f}%(高风险)")
        elif loss_ratio > max_daily_loss * 0.5:
            adjustments.append(-10)
            signals.append(f"今日亏损{loss_ratio:.1f}%接近上限{max_daily_loss:.1f}%")

    if len(recent_klines) >= 10:
        trs = []
        for i in range(1, len(recent_klines)):
            h = float(recent_klines[i].high or 0)
            l_val = float(recent_klines[i].low or 0)
            prev_c = float(recent_klines[i - 1].close or 0)
            tr = max(h - l_val, abs(h - prev_c), abs(l_val - prev_c))
            trs.append(tr)
        atr = sum(trs) / len(trs)
        atr_pct = (atr / current_price * 100) if current_price > 0 else 0
        details["atr_pct"] = round(atr_pct, 2)
        if atr_pct > 5:
            adjustments.append(-10)
            signals.append(f"ATR波动率{atr_pct:.1f}%(高波动风险)")
        elif atr_pct > 3:
            adjustments.append(-4)
            signals.append(f"ATR波动率{atr_pct:.1f}%(中等波动)")
        else:
            adjustments.append(3)
            signals.append(f"ATR波动率{atr_pct:.1f}%(波动适中)")

    risk_bl = _cfg("risk", "risk_baseline") or 80.0
    score = _normalize_score(adjustments, baseline=risk_bl)
    details["signals"] = signals

    return {
        "score": score,
        "weight": 0.25,
        "label": "风险评估",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Risk score: sync version (for Celery tasks)
# ---------------------------------------------------------------------------


def _calc_risk_score_sync(
    db,
    user: User,
    symbol: str,
    current_price: float,
) -> Dict[str, Any]:
    """Synchronous risk score calculation — canonical implementation for Celery."""
    from sqlalchemy import select as sync_select

    positions = list(db.execute(
        sync_select(Position).where(Position.user_id == user.id)
    ).scalars().all())

    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    risk_event_count = db.execute(
        sync_select(func.count()).select_from(RiskRecord).where(
            RiskRecord.user_id == user.id,
            RiskRecord.symbol == symbol,
            RiskRecord.created_at >= recent_cutoff,
        )
    ).scalar() or 0

    recent_klines = list(db.execute(
        sync_select(KLine).where(
            KLine.symbol == symbol, KLine.interval == "1d"
        ).order_by(KLine.timestamp.desc()).limit(15)
    ).scalars().all())
    recent_klines.reverse()

    return _compute_risk_score(
        positions=positions,
        user_max_position_ratio=float(user.max_position_ratio or 30),
        user_max_daily_loss=float(user.max_daily_loss or 5),
        symbol=symbol,
        current_price=current_price,
        risk_event_count=risk_event_count,
        recent_klines=recent_klines,
    )


# ---------------------------------------------------------------------------
# Risk score: async version (for FastAPI endpoints)
# ---------------------------------------------------------------------------


async def _calc_risk_score(
    db: AsyncSession,
    user: User,
    symbol: str,
    current_price: float,
) -> Dict[str, Any]:
    """Async risk score calculation for FastAPI endpoints."""

    result = await db.execute(
        select(Position).where(Position.user_id == user.id)
    )
    positions = list(result.scalars().all())

    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(func.count()).select_from(RiskRecord).where(
            RiskRecord.user_id == user.id,
            RiskRecord.symbol == symbol,
            RiskRecord.created_at >= recent_cutoff,
        )
    )
    risk_event_count = result.scalar() or 0

    result = await db.execute(
        select(KLine).where(
            KLine.symbol == symbol, KLine.interval == "1d"
        ).order_by(KLine.timestamp.desc()).limit(15)
    )
    recent_klines = list(result.scalars().all())
    recent_klines.reverse()

    return _compute_risk_score(
        positions=positions,
        user_max_position_ratio=float(user.max_position_ratio or 30),
        user_max_daily_loss=float(user.max_daily_loss or 5),
        symbol=symbol,
        current_price=current_price,
        risk_event_count=risk_event_count,
        recent_klines=recent_klines,
    )


def _calc_momentum_score(data: Dict[str, np.ndarray]) -> Dict[str, Any]:
    """Compute momentum score (0-100) from short-term price trends."""
    details: Dict[str, Any] = {}
    signals: List[str] = []
    adjustments: List[float] = []

    close = data["close"]
    n = len(close)

    if n >= 5:
        pct_5 = (float(close[-1]) / float(close[-5]) - 1) * 100
        details["momentum_5"] = round(pct_5, 2)
        if pct_5 > 3:
            adjustments.append(12)
            signals.append(f"5日动量{pct_5:.1f}%(强势上涨)")
        elif pct_5 > 1:
            adjustments.append(6)
            signals.append(f"5日动量{pct_5:.1f}%(温和上涨)")
        elif pct_5 > -1:
            adjustments.append(-2)
            signals.append(f"5日动量{pct_5:.1f}%(横盘)")
        elif pct_5 > -3:
            adjustments.append(-8)
            signals.append(f"5日动量{pct_5:.1f}%(温和下跌)")
        else:
            adjustments.append(-12)
            signals.append(f"5日动量{pct_5:.1f}%(弱势下跌)")

    if n >= 10:
        pct_10 = (float(close[-1]) / float(close[-10]) - 1) * 100
        details["momentum_10"] = round(pct_10, 2)

    # Trend consistency
    if n >= 20:
        ma5 = float(_rolling_mean(close, 5)[-1])
        ma10 = float(_rolling_mean(close, 10)[-1])
        ma20_val = float(_rolling_mean(close, 20)[-1])
        if ma5 > ma10 > ma20_val:
            adjustments.append(8)
            signals.append("多周期均线多头排列(趋势确认)")
            details["trend_alignment"] = "bullish"
        elif ma5 < ma10 < ma20_val:
            adjustments.append(-8)
            signals.append("多周期均线空头排列(下跌趋势)")
            details["trend_alignment"] = "bearish"
        else:
            details["trend_alignment"] = "mixed"
            signals.append("多周期均线排列不一致(趋势不明)")

    score = _normalize_score(adjustments)
    details["signals"] = signals

    return {
        "score": score,
        "weight": 0.10,
        "label": "动量分析",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Fundamental analysis factor (P4)
# ---------------------------------------------------------------------------


async def _afetch_fundamental(db: AsyncSession, symbol: str) -> Dict[str, Any]:
    """Async wrapper to fetch fundamental data."""
    import asyncio
    from app.services.data_provider import fetch_fundamental_data
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fetch_fundamental_data, symbol)


def _calc_fundamental_score(fund_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute fundamental score (0-100) from PE, PB, ROE, growth data."""
    details: Dict[str, Any] = {}
    signals: List[str] = []
    adjustments: List[float] = []

    if not fund_data:
        fund_data = {}

    source = fund_data.get("source", "unknown")
    from app.services.data_authenticity import REAL_SOURCES
    if source not in REAL_SOURCES:
        details["source_warning"] = f"基本面数据来源={source},非真实数据"

    pe = float(fund_data.get("pe", 0) or 0)
    pb = float(fund_data.get("pb", 0) or 0)
    roe = float(fund_data.get("roe", 0) or 0)
    revenue_growth = float(fund_data.get("revenue_growth", 0) or 0)
    profit_growth = float(fund_data.get("profit_growth", 0) or 0)

    details["pe"] = round(pe, 2)
    details["pb"] = round(pb, 2)
    details["roe"] = round(roe, 2)
    details["revenue_growth"] = round(revenue_growth, 2)
    details["profit_growth"] = round(profit_growth, 2)

    # PE valuation — A-share typical PE range 10-60, median ~25
    if pe > 0:
        if pe < 15:
            adjustments.append(12)
            signals.append(f"PE={pe:.1f} 低估值区间")
        elif pe < 25:
            adjustments.append(6)
            signals.append(f"PE={pe:.1f} 合理偏低")
        elif pe < 40:
            signals.append(f"PE={pe:.1f} 估值合理")
        elif pe < 60:
            adjustments.append(-6)
            signals.append(f"PE={pe:.1f} 估值偏高")
        else:
            adjustments.append(-12)
            signals.append(f"PE={pe:.1f} 高估值区间(风险)")
    else:
        signals.append("PE数据缺失")

    # PB valuation — A-share typical PB 1-5, below 1.5 considered cheap
    if pb > 0:
        if pb < 1.5:
            adjustments.append(5)
            signals.append(f"PB={pb:.2f} 低于净资产(安全边际高)")
        elif pb > 8:
            adjustments.append(-5)
            signals.append(f"PB={pb:.2f} 市净率偏高")

    # ROE — profitability indicator
    if roe > 0:
        if roe > 20:
            adjustments.append(10)
            signals.append(f"ROE={roe:.1f}% 高盈利能力")
        elif roe > 15:
            adjustments.append(6)
            signals.append(f"ROE={roe:.1f}% 良好盈利能力")
        elif roe > 8:
            adjustments.append(2)
            signals.append(f"ROE={roe:.1f}% 一般盈利能力")
        elif roe < 3:
            adjustments.append(-4)
            signals.append(f"ROE={roe:.1f}% 盈利能力弱")
    else:
        signals.append("ROE数据缺失")

    # Revenue growth
    if revenue_growth > 20:
        adjustments.append(8)
        signals.append(f"营收增长{revenue_growth:.1f}%(高成长)")
    elif revenue_growth > 10:
        adjustments.append(5)
        signals.append(f"营收增长{revenue_growth:.1f}%(稳定增长)")
    elif revenue_growth > 0:
        adjustments.append(2)
        signals.append(f"营收增长{revenue_growth:.1f}%(小幅增长)")
    elif revenue_growth < -10:
        adjustments.append(-7)
        signals.append(f"营收下滑{revenue_growth:.1f}%(警惕)")

    # Profit growth
    if profit_growth > 30:
        adjustments.append(6)
        signals.append(f"利润增长{profit_growth:.1f}%(高速增长)")
    elif profit_growth > 10:
        adjustments.append(3)
        signals.append(f"利润增长{profit_growth:.1f}%(稳健)")
    elif profit_growth < -20:
        adjustments.append(-8)
        signals.append(f"利润大降{profit_growth:.1f}%(基本面恶化)")

    score = _normalize_score(adjustments)
    details["signals"] = signals

    return {
        "score": score,
        "weight": 0.10,
        "label": "基本面分析",
        "details": details,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _score_to_recommendation(score: float, agreement_factor: float = 1.0) -> DecisionRecommendation:
    """
    Map composite score to recommendation, adjusting thresholds based on
    signal agreement.

    When factors disagree (low agreement_factor), the HOLD zone widens and
    extreme recommendations require stronger signals — reflecting genuine
    uncertainty rather than forcing a false-certainty label.

    Standard thresholds (agreement_factor = 1.0):
      85+ → STRONG_BUY, 65+ → BUY, 35+ → HOLD, 15+ → SELL, <15 → STRONG_SELL

    Maximum disagreement (agreement_factor = 0.4) widens HOLD by ~10 points
    and raises STRONG thresholds by ~9 points.
    """
    disagreement = 1.0 - agreement_factor  # 0 (agree) to 0.6 (max disagree)
    he_scale = _cfg("scoring", "adaptive_hold_expansion_scale") or 12
    sb_scale = _cfg("scoring", "adaptive_strong_barrier_scale") or 10
    hold_expansion = disagreement * he_scale     # Widen HOLD zone
    strong_barrier = disagreement * sb_scale     # Raise STRONG bar

    rec_sb = _cfg("scoring", "rec_strong_buy") or 85
    rec_b = _cfg("scoring", "rec_buy") or 65
    rec_h = _cfg("scoring", "rec_hold") or 35
    rec_s = _cfg("scoring", "rec_sell") or 15
    bo_ratio = _cfg("scoring", "adaptive_buy_offset_ratio") or 0.6
    ho_ratio = _cfg("scoring", "adaptive_hold_offset_ratio") or 0.6

    if score >= rec_sb + strong_barrier:
        return DecisionRecommendation.STRONG_BUY
    elif score >= rec_b + hold_expansion * bo_ratio:
        return DecisionRecommendation.BUY
    elif score >= rec_h - hold_expansion * ho_ratio:
        return DecisionRecommendation.HOLD
    elif score >= rec_s - strong_barrier:
        return DecisionRecommendation.SELL
    return DecisionRecommendation.STRONG_SELL


def _detect_regime_transition(
    close: np.ndarray, high: np.ndarray, low: np.ndarray
) -> Dict[str, Any]:
    """
    Detect whether the market is entering or exiting a trending regime.

    The most profitable inflection points occur when ADX crosses the 25
    threshold — entering a trend signals momentum opportunities; exiting
    signals mean-reversion should take priority.

    Returns: {transition: 'entering_trend'|'exiting_trend'|'steady',
              direction: 'up'|'down'|None, transition_multiplier: float}
    """
    result: Dict[str, Any] = {
        "transition": "steady", "direction": None, "transition_multiplier": 1.0,
    }

    min_bars_rt = int(_cfg("regime_detection", "min_bars_regime") or 30)
    n = len(close)
    if n < min_bars_rt:
        return result

    # Current ADX
    current_adx = _calc_adx(high, low, close)

    # Previous ADX (use data excluding the last 3-5 bars to detect change)
    rt_lookback = int(_cfg("regime_detection", "transition_lookback") or 5)
    prev_n = n - rt_lookback
    if prev_n >= min_bars_rt:
        prev_close = close[:prev_n]
        prev_high = high[:prev_n]
        prev_low = low[:prev_n]
        prev_adx = _calc_adx(prev_high, prev_low, prev_close)
    else:
        return result

    ma20 = float(_rolling_mean(close, 20)[-1])
    current_close_val = float(close[-1])

    direction = "up" if current_close_val > ma20 else "down"

    adx_thresh = _cfg("regime_detection", "adx_threshold_trending") or 25
    enter_mult = _cfg("regime_detection", "transition_enter_mult") or 1.06

    # ADX crossing above threshold → entering trend
    if prev_adx < adx_thresh and current_adx >= adx_thresh:
        result["transition"] = "entering_trend"
        result["direction"] = direction
        result["transition_multiplier"] = enter_mult
        result["signal"] = f"ADX上穿{adx_thresh}({prev_adx:.0f}→{current_adx:.0f})市场进入{'上涨' if direction == 'up' else '下跌'}趋势"

    # ADX crossing below threshold → exiting trend
    elif prev_adx >= adx_thresh and current_adx < adx_thresh:
        result["transition"] = "exiting_trend"
        result["direction"] = direction
        exit_mult = _cfg("regime_detection", "transition_exit_mult") or 0.92
        result["transition_multiplier"] = exit_mult
        result["signal"] = f"ADX下穿{adx_thresh}({prev_adx:.0f}→{current_adx:.0f})趋势减弱进入震荡"

    return result


def _calc_market_context_adjustment(close: np.ndarray) -> Dict[str, Any]:
    """
    Compute market-relative baseline adjustment.

    A stock's absolute technical score needs context: an 80 in a downtrend
    is riskier than an 80 in an uptrend. This function assesses the stock's
    position relative to its 60-period moving average and returns a modest
    adjustment (typically ±5 points).

    This is a simplified proxy for market-relative ranking — in a full
    implementation this would use a broad market index (CSI 300 / SZ50)
    for beta-relative scoring.
    """
    result: Dict[str, Any] = {"adjustment": 0.0, "position": "neutral", "signal": ""}

    ma_period = int(_cfg("market_context", "ma60_period") or 60)
    min_bars_mc = int(_cfg("market_context", "min_bars_context") or 60)
    n = len(close)
    if n < min_bars_mc:
        return result

    ma60 = float(_rolling_mean(close, ma_period)[-1])
    current = float(close[-1])
    if ma60 <= 0 or math.isnan(ma60):
        return result

    # Normalized distance from MA60 (%)
    deviation = (current / ma60 - 1.0) * 100

    result["deviation_pct"] = round(deviation, 2)
    result["ma60"] = round(ma60, 2)

    dev_15 = _cfg("market_context", "dev_above_15pct") or -4.0
    dev_5 = _cfg("market_context", "dev_above_5pct") or 2.0
    dev_m15 = _cfg("market_context", "dev_below_minus_15pct") or 3.0
    dev_m5 = _cfg("market_context", "dev_below_minus_5pct") or -3.0

    if deviation > 15:
        result["adjustment"] = dev_15
        result["position"] = "extended_up"
        result["signal"] = f"价格高于MA60 {deviation:.0f}%(过度延伸注意回调)"
    elif deviation > 5:
        result["adjustment"] = dev_5
        result["position"] = "above_ma"
        result["signal"] = f"价格高于MA60 {deviation:.0f}%(中期趋势向上)"
    elif deviation < -15:
        result["adjustment"] = dev_m15
        result["position"] = "extended_down"
        result["signal"] = f"价格低于MA60 {deviation:.0f}%(深度超跌可能反弹)"
    elif deviation < -5:
        result["adjustment"] = dev_m5
        result["position"] = "below_ma"
        result["signal"] = f"价格低于MA60 {deviation:.0f}%(中期趋势偏弱)"

    return result


def _recommendation_label(rec: DecisionRecommendation) -> str:
    mapping = {
        DecisionRecommendation.STRONG_BUY: "强烈买入",
        DecisionRecommendation.BUY: "买入",
        DecisionRecommendation.HOLD: "持有",
        DecisionRecommendation.SELL: "卖出",
        DecisionRecommendation.STRONG_SELL: "强烈卖出",
    }
    return mapping.get(rec, "未知")


def _calc_target_stop(data: Dict[str, np.ndarray], composite: float) -> Tuple[Optional[float], Optional[float]]:
    """Calculate target price and stop loss based on recent volatility."""
    close = data["close"]
    n = len(close)
    atr_period = int(_cfg("target_stop", "atr_period_ts") or 10)
    if n < atr_period:
        return None, None
    last_price = float(close[-1])
    diff_abs = np.abs(np.diff(close[-atr_period:]))
    atr_fallback = _cfg("target_stop", "atr_fallback_pct") or 0.02
    atr = float(np.mean(diff_abs)) if len(diff_abs) > 0 else last_price * atr_fallback
    if math.isnan(atr) or atr <= 0:
        atr = last_price * atr_fallback

    target_bull_thresh = _cfg("target_stop", "target_bull_threshold") or 60
    target_bull_mult = _cfg("target_stop", "target_bull_atr_mult") or 2.5
    target_mod_thresh = _cfg("target_stop", "target_moderate_threshold") or 40
    target_mod_mult = _cfg("target_stop", "target_moderate_atr_mult") or 1.5
    stop_mult = _cfg("target_stop", "stop_loss_atr_mult") or 1.5

    if composite >= target_bull_thresh:
        target_price = round(last_price + target_bull_mult * atr, 2)
    elif composite >= target_mod_thresh:
        target_price = round(last_price + target_mod_mult * atr, 2)
    else:
        target_price = None

    stop_loss = round(last_price - stop_mult * atr, 2) if composite >= target_mod_thresh else None
    return target_price, stop_loss


def _build_reasoning(
    symbol: str,
    rec: DecisionRecommendation,
    composite: float,
    factors: Dict[str, Any],
    current_price: float,
    price_date: str = "",
) -> str:
    """Build a human-readable Chinese reasoning summary."""
    label = _recommendation_label(rec)
    regime = factors.get("regime", "未知")
    regime_labels = {
        "trending_up": "上涨趋势", "trending_down": "下跌趋势",
        "ranging": "震荡整理", "volatile": "高波动",
    }
    regime_text = regime_labels.get(regime, regime)

    date_str = f" ({price_date})" if price_date else ""
    lines = [
        f"【{symbol} 投资决策分析】",
        f"综合分析日期: {price_date or 'N/A'}",
        f"综合评分: {composite:.1f}/100 → 建议: {label}",
        f"当前价格: {current_price:.2f}{date_str} | 市场状态: {regime_text}",
        "",
    ]

    for key in ("technical", "sentiment", "risk", "momentum", "fundamental"):
        fdata = factors.get(key, {})
        score_val = fdata.get("score", 0)
        label_str = fdata.get("label", key)
        lines.append(f"■ {label_str} (得分: {score_val:.0f}):")
        for sig in fdata.get("details", {}).get("signals", [])[:3]:
            lines.append(f"  - {sig}")

    # Add weekly confirmation note
    if factors.get("weekly_technical"):
        wt = factors["weekly_technical"]
        lines.append(f"\n■ 周线确认 (得分: {wt['score']:.0f}):")
        for sig in wt.get("details", {}).get("signals", [])[:2]:
            lines.append(f"  - {sig}")

    if rec in (DecisionRecommendation.STRONG_BUY, DecisionRecommendation.BUY):
        lines.append(f"\n⚠ 以上为量化分析结果，仅供参考，不构成投资建议。投资有风险，入市需谨慎。")
    elif rec in (DecisionRecommendation.SELL, DecisionRecommendation.STRONG_SELL):
        lines.append(f"\n⚠ 风险提示：当前信号偏空，建议关注风险控制。以上分析仅供参考。")
    else:
        lines.append(f"\n⚠ 当前信号中性，建议观望等待更明确的趋势信号。")

    return "\n".join(lines)


def _klines_to_arrays(klines: List[KLine]) -> Dict[str, np.ndarray]:
    """Convert ORM kline objects to dict of numpy arrays."""
    n = len(klines)
    result = {
        "close": np.zeros(n, dtype=float),
        "open": np.zeros(n, dtype=float),
        "high": np.zeros(n, dtype=float),
        "low": np.zeros(n, dtype=float),
        "volume": np.zeros(n, dtype=float),
    }
    for i, k in enumerate(klines):
        result["close"][i] = float(k.close)
        result["open"][i] = float(k.open)
        result["high"][i] = float(k.high)
        result["low"][i] = float(k.low)
        result["volume"][i] = float(k.volume or 0)
    return result


def _decision_to_dict(decision: InvestmentDecision) -> Dict[str, Any]:
    """Serialize an InvestmentDecision ORM object to a dict."""
    return {
        "id": decision.id,
        "user_id": decision.user_id,
        "symbol": decision.symbol,
        "recommendation": decision.recommendation.value if hasattr(decision.recommendation, "value") else decision.recommendation,
        "confidence": decision.confidence,
        "target_price": decision.target_price,
        "stop_loss": decision.stop_loss,
        "factors": decision.factors,
        "reasoning": decision.reasoning,
        "status": decision.status.value if hasattr(decision.status, "value") else decision.status,
        "valid_until": decision.valid_until,
        "created_at": decision.created_at,
        "updated_at": decision.updated_at,
    }
