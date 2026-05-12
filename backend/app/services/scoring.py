"""
Scoring engine — multi-factor analysis with dynamic weighting.

Categories:
  Technical     — MA, MACD, RSI, Bollinger, KDJ, volume divergence
  Sentiment     — price change, volume, bid/ask spread
  Risk          — position exposure, drawdown, volatility, risk events
  Momentum      — short-term price trend strength, MA alignment
  Fundamental   — PE, PB, ROE, revenue growth

All scoring functions return {score, weight, label, signals, details}.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import DecisionRecommendation
from app.models.market_data import KLine, Ticker
from app.models.position import Position
from app.models.risk import RiskRecord
from app.models.user import User
from app.services.decision_config import get as _cfg
from app.services.indicators import (
    _rolling_mean,
    _rolling_std,
    _ewma,
    _ema,
    _calc_kdj,
    _calc_money_flow,
    _calc_adx,
    _detect_regime,
    _detect_regime_transition,
    _detect_volume_divergence,
    _detect_macd_divergence,
    _detect_rsi_divergence,
    _calc_market_context_adjustment,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dynamic weight computation
# ---------------------------------------------------------------------------

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
# Risk scoring
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
# Fundamental analysis
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
# Score → recommendation conversion
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
