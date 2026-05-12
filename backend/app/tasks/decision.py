"""
Investment decision periodic task - generates decisions for active symbols.
Also checks historical decision outcomes for performance tracking.

Uses SyncSessionLocal and imports scoring functions from services/decision.py.
"""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from loguru import logger
from sqlalchemy import select, func, desc, and_

from app.core.celery_app import celery_app
from app.core.database import SyncSessionLocal
from app.core.redis import TaskLock
from app.models.decision import (
    DecisionRecommendation,
    DecisionStatus,
    DecisionOutcome,
    InvestmentDecision,
    OutcomeType,
)
from app.models.market_data import KLine, Ticker
from app.models.position import Position
from app.models.risk import RiskRecord
from app.models.user import User
from app.services.data_provider import refresh_all_tickers, fetch_fundamental_data
from app.services.indicators import (
    _calc_adx,
    _rolling_mean,
    _rolling_std,
    _detect_regime,
    _detect_regime_transition,
    _calc_market_context_adjustment,
)
from app.services.scoring import (
    _calc_technical_score,
    _calc_sentiment_score,
    _calc_momentum_score,
    _calc_risk_score_sync,
    _calc_fundamental_score,
    _score_to_recommendation,
    _compute_dynamic_weights,
)
from app.services.decision import (
    _calc_target_stop,
    _build_reasoning,
    _klines_to_arrays,
    _get_weekly_data_sync,
)


# ---------------------------------------------------------------------------
# Periodic decision generation
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.decision.generate_investment_decisions",
    queue="strategy",
)
def generate_investment_decisions():
    """
    Periodic decision generation task.

    Runs every 5 minutes during A-share market hours (weekdays 9:30-15:00 CST).
    """
    now = datetime.now(timezone.utc)
    china_hour = (now.hour + 8) % 24
    china_weekday = now.weekday()

    if china_weekday >= 5:
        return "Skipped: weekend"
    if china_hour < 9 or china_hour > 15:
        return "Skipped: outside market hours"

    with TaskLock("generate_investment_decisions", timeout=300) as acquired:
        if not acquired:
            return "Skipped: another instance is running"

        logger.info(f"开始生成投资决策: {now}")
        db = SyncSessionLocal()

        try:
            # Refresh latest prices
            refresh_all_tickers(db)
            db.flush()

            from app.models.market_data import SymbolInfo
            watched = list(db.execute(
                select(SymbolInfo).where(SymbolInfo.is_watched == True, SymbolInfo.status == "active")
            ).scalars().all())
            symbols = [s.symbol for s in watched]
            if not symbols:
                return "No watched symbols"

            users = list(db.execute(
                select(User).where(User.is_active == True)
            ).scalars().all())

            tickers = {
                t.symbol: t
                for t in db.execute(select(Ticker)).scalars().all()
            }

            total_generated = 0
            for user in users:
                for symbol in symbols[:10]:
                    try:
                        decision = _generate_decision_sync(db, user, symbol, tickers.get(symbol))
                        if decision:
                            db.add(decision)
                            db.flush()
                            total_generated += 1
                    except Exception as exc:
                        logger.warning(f"Decision failed for user={user.id} symbol={symbol}: {exc}")
                        continue

            # Expire old decisions
            expired = list(db.execute(
                select(InvestmentDecision).where(
                    InvestmentDecision.status == DecisionStatus.ACTIVE,
                    InvestmentDecision.valid_until < now,
                )
            ).scalars().all())
            for d in expired:
                d.status = DecisionStatus.EXPIRED
                d.updated_at = now

            db.commit()
            logger.info(f"决策生成完成: {total_generated} generated, {len(expired)} expired")
            return f"Generated {total_generated} decisions, expired {len(expired)}"

        except Exception as exc:
            db.rollback()
            logger.error(f"决策任务失败: {exc}")
            return f"Error: {exc}"
        finally:
            db.close()


def _ensure_fresh_klines_sync(db, symbol: str, interval: str):
    """Sync: ensure fresh kline data exists.

    - mock mode: regenerate mock klines from current ticker price
    - real provider mode: check for existing klines, fetch if missing or stale
    """
    from app.core.config import settings
    from app.services.data_provider import mock_market_data, fetch_real_klines
    from app.core.market_constants import MOCK_CONFIG
    from app.services.market import save_kline_data_sync

    now = datetime.now(timezone.utc)

    # Check current kline state
    latest = db.execute(
        select(KLine.timestamp)
        .where(KLine.symbol == symbol, KLine.interval == interval)
        .order_by(KLine.timestamp.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest and latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)

    klines_fresh = latest and (now - latest).total_seconds() < 21600

    # --- Mock mode: regenerate from ticker price ---
    if settings.MARKET_DATA_PROVIDER in ("mock", ""):
        if klines_fresh:
            return

        config = MOCK_CONFIG.get(symbol, {"base_price": 100.0, "days": 90, "interval_minutes": 60})

        ticker = db.execute(
            select(Ticker).where(Ticker.symbol == symbol)
        ).scalar_one_or_none()
        base_price = float(ticker.last_price) if ticker and ticker.last_price else config["base_price"]

        db.execute(
            KLine.__table__.delete().where(KLine.symbol == symbol, KLine.interval == interval)
        )
        db.flush()

        data = mock_market_data(
            base_price=base_price,
            days=120,
            interval_minutes=1440,
            volatility=0.02,
        )
        save_kline_data_sync(db, symbol, interval, data)
        return

    # --- Real data provider mode ---
    if klines_fresh:
        return

    # Fetch real klines from configured provider (sina / eastmoney / akshare)
    kline_data = fetch_real_klines(symbol, interval)
    if kline_data:
        saved = save_kline_data_sync(db, symbol, interval, kline_data)
        logger.info("为 %s:%s 获取了 %s 条真实K线数据", symbol, interval, saved)
    elif not latest:
        logger.warning(
            "%s:%s 无K线数据 — 真实数据源(%s)获取失败，跳过决策生成",
            symbol, interval, settings.MARKET_DATA_PROVIDER,
        )


def _generate_decision_sync(db, user: User, symbol: str, ticker=None):
    """Generate a single InvestmentDecision synchronously (Celery context).

    Args:
        db: sync DB session.
        user: user to generate decision for.
        symbol: stock/ETF code.
        ticker: pre-fetched Ticker object (optional, to avoid N+1 query).
    """

    # Refresh stale kline data so prices are current
    _ensure_fresh_klines_sync(db, symbol, "1d")

    # Fetch most recent daily kline data (newest 200, ascending order)
    klines_raw = list(db.execute(
        select(KLine).where(
            KLine.symbol == symbol,
            KLine.interval == "1d",
        ).order_by(KLine.timestamp.desc()).limit(200)
    ).scalars().all())
    klines_raw.reverse()  # ascending order for chronological analysis

    if len(klines_raw) < 20:
        return None

    data = _klines_to_arrays(klines_raw)
    kline_close = float(data["close"][-1])

    # 优先使用实时ticker价格，仅当ticker不可靠时才用K线收盘价
    from app.services.data_authenticity import verify_data_source, REAL_SOURCES

    if ticker and ticker.last_price and ticker.data_source in REAL_SOURCES:
        current_price = float(ticker.last_price)
    elif ticker and ticker.last_price:
        current_price = kline_close
        ticker.last_price = current_price
        ticker.updated_at = datetime.now(timezone.utc)
        if klines_raw:
            kline_source = getattr(klines_raw[-1], 'data_source', 'unknown')
            if kline_source in REAL_SOURCES and (not ticker.data_source or ticker.data_source == 'unknown'):
                ticker.data_source = kline_source
        db.flush()
    else:
        current_price = kline_close

    # 追踪数据来源
    if klines_raw:
        kline_source = getattr(klines_raw[-1], 'data_source', 'unknown')
        if not verify_data_source(kline_source, operation=f"生成{symbol}投资决策"):
            logger.warning(
                "%s 的投资决策基于来源为 '%s' 的数据，结果可能不可靠",
                symbol, kline_source,
            )

    # Market regime detection and continuous dynamic weights (P1)
    regime = _detect_regime(data)
    adx_val = _calc_adx(
        data["high"], data["low"], data["close"]
    ) if "high" in data and "low" in data else 25.0
    ma20 = float(_rolling_mean(data["close"], 20)[-1]) if len(data["close"]) >= 20 else current_price
    bb_std = float(_rolling_std(data["close"], 20, ddof=0)[-1]) if len(data["close"]) >= 20 else 0
    bb_width = (2.0 * bb_std) / ma20 if not math.isnan(bb_std) and ma20 > 0 else 0.05
    weights = _compute_dynamic_weights(adx_val, bb_width, current_price, ma20)

    # Multi-factor scoring
    technical_result = _calc_technical_score(data)
    sentiment_result = _calc_sentiment_score(ticker)
    risk_result = _calc_risk_score_sync(db, user, symbol, current_price)
    momentum_result = _calc_momentum_score(data)

    # Fundamental factor (P4)
    fund_data = fetch_fundamental_data(symbol)
    fundamental_result = _calc_fundamental_score(fund_data)

    # Multi-timeframe: fetch weekly data and compute weekly technical (P2)
    weekly_technical = None
    tech_score_for_composite = technical_result["score"]
    try:
        weekly_data = _get_weekly_data_sync(db, symbol)
        if weekly_data and len(weekly_data["close"]) >= 20:
            weekly_technical = _calc_technical_score(weekly_data)
    except Exception:
        pass

    # Integrate weekly as a sub-factor within technical (70% daily + 30% weekly)
    if weekly_technical:
        tech_score_for_composite = round(
            technical_result["score"] * 0.70 + weekly_technical["score"] * 0.30, 1
        )

    # Weighted composite (linear combination)
    raw_weighted = (
        tech_score_for_composite * weights["technical"]
        + sentiment_result["score"] * weights["sentiment"]
        + risk_result["score"] * weights["risk"]
        + momentum_result["score"] * weights["momentum"]
        + fundamental_result["score"] * weights["fundamental"]
    )

    # Signal disagreement penalty (P5)
    factor_scores = np.array([
        tech_score_for_composite,
        sentiment_result["score"],
        risk_result["score"],
        momentum_result["score"],
        fundamental_result["score"],
    ])
    score_std = float(np.std(factor_scores))
    disagreement_penalty = min(score_std * 0.30, 15.0)
    composite = round(raw_weighted - disagreement_penalty, 1)
    composite = max(0.0, min(100.0, composite))

    # True confidence: separated from composite score
    agreement_factor = max(0.4, 1.0 - score_std / 80.0)
    confidence = min(100, max(0, int(composite * agreement_factor)))
    if confidence < 10 and composite > 20:
        confidence = 10

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

    latest_kline_ts = klines_raw[-1].timestamp if klines_raw else None
    if latest_kline_ts:
        if latest_kline_ts.tzinfo is None:
            latest_kline_ts = latest_kline_ts.replace(tzinfo=timezone.utc)
        cst = timezone(timedelta(hours=8))
        price_date = latest_kline_ts.astimezone(cst).strftime("%Y-%m-%d")
    else:
        price_date = ""

    reasoning = _build_reasoning(symbol, recommendation, composite, factors, current_price, price_date)

    # Expire any existing active decisions for this stock before creating a new one
    db.execute(
        InvestmentDecision.__table__.update()
        .where(
            InvestmentDecision.user_id == user.id,
            InvestmentDecision.symbol == symbol,
            InvestmentDecision.status == DecisionStatus.ACTIVE,
        )
        .values(status=DecisionStatus.EXPIRED)
    )
    db.flush()

    return InvestmentDecision(
        user_id=user.id,
        symbol=symbol,
        recommendation=recommendation,
        confidence=confidence,
        target_price=target_price,
        stop_loss=stop_loss,
        factors=factors,
        reasoning=reasoning,
        status=DecisionStatus.ACTIVE,
        valid_until=datetime.now(timezone.utc) + timedelta(hours=24),
    )


# ---------------------------------------------------------------------------
# Decision outcome checking (P3)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.decision.check_decision_outcomes",
    queue="strategy",
)
def check_decision_outcomes():
    """
    Check outcomes of decisions made 24+ hours ago.

    Compares target/stop prices against actual price movement over the
    decision's valid window. Writes DecisionOutcome records.
    Runs every 30 minutes.
    """
    with TaskLock("check_decision_outcomes", timeout=600) as acquired:
        if not acquired:
            return "Skipped: another instance is running"

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=20)

        logger.info(f"开始检查决策结果: {now}")
        db = SyncSessionLocal()

        try:
            # Find decisions that are active or executed and past their valid_until
            decisions = list(db.execute(
                select(InvestmentDecision).where(
                    and_(
                        InvestmentDecision.status.in_([
                            DecisionStatus.ACTIVE,
                            DecisionStatus.EXECUTED,
                        ]),
                        InvestmentDecision.valid_until < cutoff,
                        InvestmentDecision.valid_until > now - timedelta(days=7),
                    )
                )
            ).scalars().all())

            outcomes_created = 0
            for decision in decisions:
                try:
                    # Check if outcome already exists
                    existing = db.execute(
                        select(DecisionOutcome).where(
                            DecisionOutcome.decision_id == decision.id
                        )
                    ).scalar_one_or_none()
                    if existing:
                        continue

                    outcome = _check_single_outcome(db, decision, now)
                    if outcome:
                        db.add(outcome)
                        db.flush()
                        outcomes_created += 1

                        # Update decision status
                        if outcome.hit_target or outcome.hit_stop:
                            decision.status = DecisionStatus.EXECUTED
                        else:
                            decision.status = DecisionStatus.EXPIRED
                        decision.updated_at = now

                except Exception as exc:
                    logger.warning(f"Outcome check failed for decision {decision.id}: {exc}")
                    continue

            # Data-quality audit: flag decisions whose target price is >50% off from real close
            try:
                active_decisions = db.execute(
                    select(InvestmentDecision).where(
                        InvestmentDecision.status == DecisionStatus.ACTIVE
                    )
                ).scalars().all()
                expired_suspicious = 0
                for d in active_decisions:
                    last_close_row = db.execute(
                        select(KLine.close)
                        .where(KLine.symbol == d.symbol, KLine.interval == "1d")
                        .order_by(KLine.timestamp.desc())
                        .limit(1)
                    ).scalar_one_or_none()
                    if last_close_row and last_close_row > 0 and d.target_price:
                        if abs(d.target_price - last_close_row) / last_close_row > 0.50:
                            logger.warning(
                                "可疑决策 #{} {}: 目标价 {:.2f} vs K线收盘 {:.2f} 偏差 {:.0f}%, 自动过期",
                                d.id, d.symbol, d.target_price, last_close_row,
                                abs(d.target_price - last_close_row) / last_close_row * 100
                            )
                            d.status = DecisionStatus.EXPIRED
                            d.updated_at = now
                            expired_suspicious += 1
                if expired_suspicious > 0:
                    logger.info("决策审计: {} 条活跃中 {} 条因数据异常被自动过期", len(active_decisions), expired_suspicious)
            except Exception:
                logger.debug("决策审计跳过: {}", exc_info=True)

            db.commit()
            logger.info(f"决策结果检查完成: {outcomes_created} outcomes created")
            return f"Checked {len(decisions)} decisions, {outcomes_created} outcomes created"

        except Exception as exc:
            db.rollback()
            logger.error(f"结果检查任务失败: {exc}")
            return f"Error: {exc}"
        finally:
            db.close()


def _check_single_outcome(db, decision: InvestmentDecision, now: datetime):
    """
    Check the outcome of a single decision by looking at actual price movement.

    Fetches kline data between decision creation and valid_until to determine
    if target or stop was hit.
    """
    symbol = decision.symbol
    created_at = decision.created_at

    # Remove timezone info for SQLite compatibility if needed
    valid_until = decision.valid_until if decision.valid_until else now

    # Query klines during the decision window
    klines = list(db.execute(
        select(KLine).where(
            KLine.symbol == symbol,
            KLine.interval == "1d",
            KLine.timestamp >= created_at - timedelta(hours=1),
            KLine.timestamp <= valid_until + timedelta(hours=2),
        ).order_by(KLine.timestamp.asc())
    ).scalars().all())

    if not klines:
        # Fall back: check current ticker
        ticker = db.execute(
            select(Ticker).where(Ticker.symbol == symbol)
        ).scalar_one_or_none()
        if not ticker:
            return None
        actual_high = float(ticker.high_24h or ticker.last_price or 0)
        actual_low = float(ticker.low_24h or ticker.last_price or 0)
        actual_close = float(ticker.last_price or 0)
    else:
        highs = [float(k.high) for k in klines]
        lows = [float(k.low) for k in klines]
        closes = [float(k.close) for k in klines]
        actual_high = max(highs)
        actual_low = min(lows)
        actual_close = closes[-1] if closes else 0

    # Get entry price from the first kline or target context
    entry_price = None
    if decision.factors and isinstance(decision.factors, dict):
        sent = decision.factors.get("sentiment", {})
        entry_price = float(sent.get("details", {}).get("last_price", 0) or 0)
    if not entry_price and klines:
        entry_price = float(klines[0].close if len(klines) > 0 else 0)
    if not entry_price:
        entry_price = float(decision.target_price or 0) * 0.95 if decision.target_price else 0

    target = float(decision.target_price or 0)
    stop = float(decision.stop_loss or 0)

    hit_target = target > 0 and actual_high >= target
    hit_stop = stop > 0 and actual_low <= stop

    # Determine outcome
    if hit_target and not hit_stop:
        outcome = OutcomeType.WIN
        if entry_price > 0:
            pnl_pct = round((target - entry_price) / entry_price * 100, 2)
        else:
            pnl_pct = 2.0
    elif hit_stop and not hit_target:
        outcome = OutcomeType.LOSS
        if entry_price > 0:
            pnl_pct = round((stop - entry_price) / entry_price * 100, 2)
        else:
            pnl_pct = -2.0
    elif hit_target and hit_stop:
        # Both hit — check which was first
        outcome = OutcomeType.BREAKEVEN
        pnl_pct = 0.0
    else:
        # Neither hit — compare close to entry
        if entry_price > 0 and actual_close > 0:
            pnl_pct = round((actual_close - entry_price) / entry_price * 100, 2)
        else:
            pnl_pct = 0.0
        if pnl_pct > 0.5:
            outcome = OutcomeType.WIN
        elif pnl_pct < -0.5:
            outcome = OutcomeType.LOSS
        else:
            outcome = OutcomeType.BREAKEVEN

    return DecisionOutcome(
        decision_id=decision.id,
        symbol=symbol,
        recommendation=decision.recommendation.value if hasattr(decision.recommendation, "value") else str(decision.recommendation),
        confidence=decision.confidence,
        entry_price=round(entry_price, 2) if entry_price else None,
        actual_high_24h=round(actual_high, 2),
        actual_low_24h=round(actual_low, 2),
        actual_close_24h=round(actual_close, 2),
        hit_target=hit_target,
        hit_stop=hit_stop,
        pnl_pct=pnl_pct,
        outcome=outcome,
        checked_at=now,
    )
