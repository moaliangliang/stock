"""
Investment decision service — core decision management and CRUD.

Scoring is delegated to:
  app.services.indicators   — pure technical indicator functions
  app.services.scoring      — multi-factor scoring engine
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import DecisionRecommendation, DecisionStatus, InvestmentDecision
from app.models.market_data import KLine, Ticker
from app.models.position import Position
from app.models.risk import RiskRecord
from app.models.user import User
from app.services.indicators import (
    _rolling_mean,
    _rolling_std,
    _calc_adx,
    _calc_kdj,
    _calc_money_flow,
    _detect_regime,
    _detect_regime_transition,
    _detect_volume_divergence,
    _detect_macd_divergence,
    _detect_rsi_divergence,
    _calc_market_context_adjustment,
)
from app.services.scoring import (
    DEFAULT_WEIGHTS,
    _compute_dynamic_weights,
    _normalize_score,
    _calc_technical_score,
    _calc_sentiment_score,
    _compute_risk_score,
    _calc_risk_score_sync,
    _calc_risk_score,
    _calc_momentum_score,
    _afetch_fundamental,
    _calc_fundamental_score,
    _score_to_recommendation,
    _apply_correlation_discount,
)
from app.services.market import get_kline_data, get_ticker, save_kline_data
from app.services.notification import create_notification
from app.services.decision_config import get as _cfg

logger = logging.getLogger(__name__)


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

    # Batch-load latest close prices for all symbols in one query
    symbols = list({d.symbol for d in active if d.symbol})
    close_map: dict[str, float] = {}
    if symbols:
        from sqlalchemy import literal_column
        sub = (
            select(
                KLine.symbol,
                KLine.close,
                func.row_number().over(
                    partition_by=KLine.symbol,
                    order_by=KLine.timestamp.desc(),
                ).label("rn"),
            )
            .where(KLine.symbol.in_(symbols), KLine.interval == "1d")
            .subquery()
        )
        rows = await db.execute(
            select(sub.c.symbol, sub.c.close).where(sub.c.rn == 1)
        )
        for row in rows.all():
            if row[1] and row[1] > 0:
                close_map[row[0]] = float(row[1])

    for d in active:
        latest_close = close_map.get(d.symbol)
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
    # True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    high_arr = data.get("high")
    low_arr = data.get("low")
    if high_arr is not None and low_arr is not None and len(high_arr) >= atr_period and len(low_arr) >= atr_period:
        tr_values = []
        for i in range(-atr_period, 0):
            h, l = float(high_arr[i]), float(low_arr[i])
            pc = float(close[i - 1]) if i > -len(close) else float(close[i])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_values.append(tr)
        atr = float(np.mean(tr_values))
    else:
        diff_abs = np.abs(np.diff(close[-atr_period:]))
        atr = float(np.mean(diff_abs)) if len(diff_abs) > 0 else last_price * atr_fallback
    atr_fallback = _cfg("target_stop", "atr_fallback_pct") or 0.02
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
