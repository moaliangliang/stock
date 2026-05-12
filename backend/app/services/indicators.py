"""
Technical indicators library — pure NumPy implementations.

All functions in this module are stateless and side-effect-free.
No database or async dependencies.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.decision_config import get as _cfg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# NumPy rolling / smoothing helpers
# ---------------------------------------------------------------------------

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
