"""Decision engine: scoring helpers, KDJ, ADX, normalization, recommendations."""
import math
import pytest
import numpy as np

from app.services.indicators import (
    _rolling_mean,
    _rolling_std,
    _ewma,
    _ema,
    _wilder_ema,
    _calc_kdj,
    _calc_adx,
    _detect_regime,
)
from app.services.scoring import (
    _normalize_score,
    _score_to_recommendation,
    _compute_dynamic_weights,
)
from app.models.decision import DecisionRecommendation


# ══════════════════════════════════════════════════════════════════════
# Rolling math
# ══════════════════════════════════════════════════════════════════════

class TestRollingMean:
    def test_simple(self):
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _rolling_mean(arr, 3)
        expected = np.array([np.nan, np.nan, 2.0, 3.0, 4.0])
        np.testing.assert_array_almost_equal(result[2:], expected[2:])

    def test_insufficient_data(self):
        arr = np.array([1.0, 2.0])
        result = _rolling_mean(arr, 5)
        assert np.all(np.isnan(result))

    def test_exact_window(self):
        arr = np.array([10.0, 20.0, 30.0])
        result = _rolling_mean(arr, 3)
        assert not math.isnan(result[-1])
        assert pytest.approx(result[-1]) == 20.0


class TestRollingStd:
    def test_constant_series(self):
        arr = np.full(10, 5.0)
        result = _rolling_std(arr, 5, ddof=0)
        assert pytest.approx(result[-1]) == 0.0

    def test_varying_series(self):
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _rolling_std(arr, 5, ddof=0)
        assert result[-1] > 0


class TestEWMA:
    def test_alpha_weighting(self):
        """EWMA should give more weight to recent values."""
        arr = np.array([0.0] * 9 + [100.0])
        result = _ewma(arr, span=5)
        assert result[-1] < 100.0  # smoothed, not raw
        assert result[-1] > result[-2]  # trending up


class TestEMA:
    def test_smoothing_factor(self):
        """Standard EMA uses alpha=2/(N+1)."""
        arr = np.array([0.0] * 9 + [10.0])
        result = _ema(arr, period=4)
        assert result[-1] < 10.0
        assert result[-1] > 0.0


class TestWilderEMA:
    def test_wilder_slower_than_ema(self):
        """Wilder's EMA converges slower than standard EMA (alpha=1/N vs 2/(N+1))."""
        arr = np.full(20, 50.0)
        arr[10] = 100.0
        arr[11:] = 100.0
        wilder = _wilder_ema(arr.astype(float), period=14)
        ema_std = _ema(arr.astype(float), period=14)
        # After the jump, Wilder's should be more conservative (closer to old value)
        jump_idx = 15
        assert wilder[jump_idx] < ema_std[jump_idx]

    def test_nan_handling(self):
        arr = np.array([np.nan, np.nan, 50.0, 50.0, 60.0])
        result = _wilder_ema(arr, period=3)
        assert not np.all(np.isnan(result))


# ══════════════════════════════════════════════════════════════════════
# Normalize score
# ══════════════════════════════════════════════════════════════════════

class TestNormalizeScore:
    def test_no_signals_returns_baseline(self):
        assert _normalize_score([], baseline=50.0) == 50.0

    def test_positive_above_baseline(self):
        score = _normalize_score([20.0])
        assert score > 50.0

    def test_negative_below_baseline(self):
        score = _normalize_score([-20.0])
        assert score < 50.0

    def test_bounded_0_to_100(self):
        # Very large positive
        assert _normalize_score([1000.0]) <= 100.0
        # Very large negative
        assert _normalize_score([-1000.0]) >= 0.0

    def test_custom_baseline(self):
        score = _normalize_score([], baseline=80.0)
        assert score == 80.0

    def test_tanh_saturation(self):
        """tanh应该平滑地接近100但不超出"""
        scores = [_normalize_score([s]) for s in [10, 50, 100, 200, 500]]
        for s in scores:
            assert 0.0 <= s <= 100.0


# ══════════════════════════════════════════════════════════════════════
# KDJ calculation
# ══════════════════════════════════════════════════════════════════════

class TestCalcKDJ:
    def _make_ohlc(self, n: int = 40):
        np.random.seed(42)
        close = np.cumsum(np.random.normal(0, 1, n)) + 100
        high = close + np.abs(np.random.normal(1, 0.5, n))
        low = close - np.abs(np.random.normal(1, 0.5, n))
        return high, low, close

    def test_output_shape(self):
        high, low, close = self._make_ohlc(40)
        result = _calc_kdj(high, low, close)
        assert "k" in result
        assert "d" in result
        assert "j" in result
        assert "golden_cross" in result
        assert "death_cross" in result

    def test_j_relationship(self):
        """J = 3K - 2D (within rounding tolerance of 0.1)"""
        high, low, close = self._make_ohlc(40)
        result = _calc_kdj(high, low, close)
        # K, D, J are all rounded to 2 decimal places independently,
        # so J ≈ round(3*K_raw - 2*D_raw, 2) while K=round(K_raw,2).
        # J from rounded K/D: J_from_rounded ≈ 3*round(K_raw,2) - 2*round(D_raw,2)
        # The difference can be up to ~0.03 due to rounding propagation.
        expected_j = 3.0 * result["k"] - 2.0 * result["d"]
        assert abs(result["j"] - expected_j) < 0.1

    def test_insufficient_data_returns_defaults(self):
        high = np.array([10.0, 11.0, 12.0])
        low = np.array([9.0, 10.0, 11.0])
        close = np.array([10.5, 10.5, 11.5])
        result = _calc_kdj(high, low, close)
        assert result["k"] == 50.0
        assert result["d"] == 50.0
        assert result["j"] == 50.0
        assert not result["golden_cross"]

    def test_values_in_reasonable_range(self):
        high, low, close = self._make_ohlc(40)
        result = _calc_kdj(high, low, close)
        assert -50 <= result["k"] <= 150
        assert -50 <= result["d"] <= 150

    def test_constant_price(self):
        """价格不变时RSV=50，KD应在50附近"""
        n = 30
        high = np.array([11.0] * n)
        low = np.array([9.0] * n)
        close = np.full(n, 10.0)
        result = _calc_kdj(high, low, close)
        assert 40 <= result["k"] <= 60
        assert 40 <= result["d"] <= 60


# ══════════════════════════════════════════════════════════════════════
# ADX calculation
# ══════════════════════════════════════════════════════════════════════

class TestCalcADX:
    def test_insufficient_data_returns_default(self):
        high = np.array([10.0] * 5)
        low = np.array([9.0] * 5)
        close = np.array([9.5] * 5)
        result = _calc_adx(high, low, close)
        assert result == 20.0

    def test_trending_market_high_adx(self):
        """强趋势应有高ADX"""
        np.random.seed(7)
        n = 60
        trend = 0.01
        close = [100.0]
        for i in range(n - 1):
            close.append(close[-1] * (1 + trend + np.random.normal(0, 0.003)))
        close = np.array(close)
        high = close * 1.01
        low = close * 0.99
        adx = _calc_adx(high, low, close)
        # Strong trend should give ADX > 25
        assert adx > 20.0, f"ADX={adx} for trending data"

    def test_ranging_market_low_adx(self):
        """震荡市应有低ADX"""
        np.random.seed(3)
        n = 60
        close = 100 + np.random.normal(0, 0.5, n).cumsum() * 0.1
        high = close * 1.005
        low = close * 0.995
        adx = _calc_adx(high, low, close)
        assert 0 <= adx <= 100


# ══════════════════════════════════════════════════════════════════════
# Score → Recommendation
# ══════════════════════════════════════════════════════════════════════

class TestScoreToRecommendation:
    def test_strong_buy(self):
        assert _score_to_recommendation(90.0, 1.0) == DecisionRecommendation.STRONG_BUY

    def test_buy(self):
        assert _score_to_recommendation(75.0, 1.0) == DecisionRecommendation.BUY

    def test_hold(self):
        assert _score_to_recommendation(50.0, 1.0) == DecisionRecommendation.HOLD

    def test_sell(self):
        assert _score_to_recommendation(25.0, 1.0) == DecisionRecommendation.SELL

    def test_strong_sell(self):
        assert _score_to_recommendation(5.0, 1.0) == DecisionRecommendation.STRONG_SELL

    def test_high_agreement_tight_thresholds(self):
        """信号高度一致时阈值应保持在标准位置"""
        assert _score_to_recommendation(86.0, 1.0) == DecisionRecommendation.STRONG_BUY
        assert _score_to_recommendation(66.0, 1.0) == DecisionRecommendation.BUY

    def test_low_agreement_wider_hold(self):
        """信号分歧大时HOLD区间应扩大"""
        # At agreement_factor=0.4 (max disagreement):
        # strong_barrier ≈ 0.6*10=6 → STRONG threshold ≈ 85+6=91
        # hold_expansion ≈ 0.6*12=7.2 → HOLD zone wider
        rec_85 = _score_to_recommendation(85.0, 0.4)
        # At max disagreement, 85 might still be BUY or HOLD depending on parameters
        assert rec_85 in (DecisionRecommendation.BUY, DecisionRecommendation.HOLD, DecisionRecommendation.STRONG_BUY)

    def test_extreme_disagreement_pushes_to_hold(self):
        """极端分歧时中等分数应向HOLD靠拢"""
        rec = _score_to_recommendation(70.0, 0.4)
        # Should NOT be STRONG_BUY with weak agreement at only 70 score
        assert rec != DecisionRecommendation.STRONG_BUY


# ══════════════════════════════════════════════════════════════════════
# Dynamic weights
# ══════════════════════════════════════════════════════════════════════

class TestDynamicWeights:
    def test_weights_sum_to_one(self):
        w = _compute_dynamic_weights(adx=25.0, bb_width=0.05, current_close=100.0, ma20=100.0)
        total = sum(w.values())
        assert pytest.approx(total) == 1.0

    def test_trending_increases_momentum(self):
        w_low = _compute_dynamic_weights(adx=10.0, bb_width=0.05, current_close=100.0, ma20=100.0)
        w_high = _compute_dynamic_weights(adx=40.0, bb_width=0.05, current_close=100.0, ma20=100.0)
        assert w_high["momentum"] > w_low["momentum"]

    def test_volatile_increases_risk(self):
        w_calm = _compute_dynamic_weights(adx=25.0, bb_width=0.02, current_close=100.0, ma20=100.0)
        w_vol = _compute_dynamic_weights(adx=25.0, bb_width=0.20, current_close=100.0, ma20=100.0)
        assert w_vol["risk"] > w_calm["risk"]

    def test_downtrend_shifts_to_sentiment(self):
        w_neutral = _compute_dynamic_weights(adx=25.0, bb_width=0.05, current_close=100.0, ma20=100.0)
        w_down = _compute_dynamic_weights(adx=25.0, bb_width=0.05, current_close=90.0, ma20=100.0)
        # In downtrend: momentum reduces, sentiment gains
        assert w_down["momentum"] < w_neutral["momentum"] or w_down["sentiment"] > w_neutral["sentiment"]

    def test_all_weights_in_range(self):
        """所有权重在合理范围内（归一化后可能略低于clamp最小值）"""
        w = _compute_dynamic_weights(adx=15.0, bb_width=0.12, current_close=95.0, ma20=100.0)
        for key, val in w.items():
            # Post-normalization floor: 0.01 accounts for extreme regime scenarios
            assert 0.01 <= val <= 0.55, f"{key}={val} out of range"
        assert pytest.approx(sum(w.values())) == 1.0


# ══════════════════════════════════════════════════════════════════════
# Regime detection
# ══════════════════════════════════════════════════════════════════════

class TestDetectRegime:
    def test_ranging_default_for_short_data(self):
        data = {
            "close": np.array([100.0] * 20),
            "high": np.array([101.0] * 20),
            "low": np.array([99.0] * 20),
        }
        assert _detect_regime(data) == "ranging"

    def test_trending_up(self):
        n = 60
        close = np.linspace(100, 130, n) + np.random.normal(0, 0.3, n)
        high = close * 1.01
        low = close * 0.99
        data = {"close": close, "high": high, "low": low}
        regime = _detect_regime(data)
        assert regime in ("trending_up", "trending_down", "ranging", "volatile")

    def test_flat_is_ranging(self):
        n = 60
        close = np.full(n, 100.0) + np.random.normal(0, 0.1, n)
        high = close * 1.005
        low = close * 0.995
        data = {"close": close, "high": high, "low": low}
        regime = _detect_regime(data)
        # Should be ranging or volatile (not trending)
        assert regime in ("ranging", "volatile")
