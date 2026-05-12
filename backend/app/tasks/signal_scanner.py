"""
信号扫描 Celery 任务 — 检测买入信号并通过 Bark 推送通知。

检测信号:
  - KDJ 超卖金叉 (J < 20 + K上穿D)
  - RSI 超卖 (RSI < 30)
  - MA 金叉 (MA5上穿MA20)
  - MACD 底背离
  - 多信号共振 (2+ 信号同时触发 → 强买入)

Bark 推送配置来自 settings，与 stock-push.sh 共用同一 KEY。
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np
import requests
from loguru import logger
from sqlalchemy import select

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.database import SyncSessionLocal
from app.core.redis import acquire_sync_lock, release_sync_lock
from app.models.market_data import KLine, SymbolInfo
from app.models.notification import Notification

BARK_URL = "https://api.day.app/push"
# BARK_KEY 从 settings 读取，见 app/core/config.py


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.zeros_like(arr)
    out[0] = arr[0]
    m = 2 / (period + 1)
    for i in range(1, len(arr)):
        out[i] = (arr[i] - out[i - 1]) * m + out[i - 1]
    return out


def _wilder_ema(arr: np.ndarray, period: int) -> np.ndarray:
    """Welles Wilder smoothing: alpha = 1/period."""
    out = np.full_like(arr, np.nan, dtype=float)
    for i in range(len(arr)):
        if not np.isnan(arr[i]):
            out[i] = float(arr[i])
            break
    alpha = 1.0 / period
    for i in range(1, len(arr)):
        if not np.isnan(arr[i]) and not np.isnan(out[i - 1]):
            out[i] = alpha * float(arr[i]) + (1 - alpha) * out[i - 1]
        elif not np.isnan(arr[i]):
            out[i] = float(arr[i])
    return out


def _compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    delta = np.diff(closes, prepend=closes[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = _wilder_ema(gain, period)[-1]
    avg_loss = _wilder_ema(loss, period)[-1]
    if not np.isnan(avg_gain) and not np.isnan(avg_loss) and avg_loss > 0:
        return float(100.0 - (100.0 / (1.0 + avg_gain / avg_loss)))
    return 100.0 if avg_loss == 0 else 50.0


def _compute_kdj(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 n: int = 9, k_p: int = 3, d_p: int = 3) -> Dict[str, Any]:
    """Full KDJ with rolling K/D — matches decision engine."""
    length = len(closes)
    if length < n + max(k_p, d_p) + 1:
        return {"k": 50.0, "d": 50.0, "j": 50.0, "golden_cross": False, "death_cross": False}

    k_vals = np.full(length, np.nan)
    d_vals = np.full(length, np.nan)
    for i in range(n - 1, length):
        h = float(np.max(highs[i - n + 1 : i + 1]))
        l = float(np.min(lows[i - n + 1 : i + 1]))
        rsv = (float(closes[i]) - l) / (h - l) * 100.0 if h != l else 50.0
        if i == n - 1:
            k_vals[i] = rsv
            d_vals[i] = rsv
        else:
            k_vals[i] = (k_p - 1) / k_p * k_vals[i - 1] + (1.0 / k_p) * rsv
            d_vals[i] = (d_p - 1) / d_p * d_vals[i - 1] + (1.0 / d_p) * k_vals[i]

    k_now = float(k_vals[-1])
    d_now = float(d_vals[-1])
    k_prev = float(k_vals[-2])
    d_prev = float(d_vals[-2])
    j_now = 3.0 * k_now - 2.0 * d_now
    return {
        "k": round(k_now, 2), "d": round(d_now, 2), "j": round(j_now, 2),
        "golden_cross": k_prev <= d_prev and k_now > d_now,
        "death_cross": k_prev >= d_prev and k_now < d_now,
    }


def _detect_macd_divergence(closes: np.ndarray) -> bool:
    """Detect bullish MACD divergence (price lower low, MACD hist higher low)."""
    n = len(closes)
    if n < 40:
        return False
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    signal = _ema(ema12 - ema26, 9)
    hist = ema12 - ema26 - signal

    lookback = min(25, n - 5)
    recent_close = closes[-lookback:]
    recent_hist = hist[-lookback:]

    price_troughs = []
    macd_troughs = []
    for i in range(2, len(recent_close) - 2):
        if (recent_close[i] < recent_close[i - 1] and recent_close[i] < recent_close[i - 2]
            and recent_close[i] < recent_close[i + 1] and recent_close[i] < recent_close[i + 2]):
            price_troughs.append((i, float(recent_close[i]), float(recent_hist[i])))
        if (recent_hist[i] < recent_hist[i - 1] and recent_hist[i] < recent_hist[i - 2]
            and recent_hist[i] < recent_hist[i + 1] and recent_hist[i] < recent_hist[i + 2]):
            macd_troughs.append((i, float(recent_close[i]), float(recent_hist[i])))

    if len(price_troughs) >= 2 and len(macd_troughs) >= 2:
        p1, p2 = price_troughs[-2], price_troughs[-1]
        m1, m2 = macd_troughs[-2], macd_troughs[-1]
        if p2[1] < p1[1] and m2[2] > m1[2]:
            return True
    return False


def confirm_multi_timeframe(symbol: str, db, daily_kline_count: int = 0) -> float:
    """
    Multi-timeframe confirmation: check 5min/15min trend alignment with daily signal.
    Returns a confidence multiplier: >1.0 = confirmed, <1.0 = weak, 0 = rejected.
    """
    try:
        # Load 5min K-lines (last 1 day = ~48 bars) and 15min (last 3 days = ~48 bars)
        short_rows = db.execute(
            select(KLine)
            .where(KLine.symbol == symbol, KLine.interval.in_(["5m", "15m"]))
            .order_by(KLine.interval.asc(), KLine.timestamp.desc())
            .limit(120)
        ).scalars().all()

        m5_bars = []
        m15_bars = []
        for r in short_rows:
            bar = {"close": r.close, "high": r.high, "low": r.low, "volume": r.volume}
            if r.interval == "5m":
                m5_bars.append(bar)
            else:
                m15_bars.append(bar)

        score = 1.0

        # Check 5min: price relative to short-term MA, recent momentum
        if len(m5_bars) >= 20:
            m5_closes = np.array([b["close"] for b in m5_bars[:20]])
            m5_ma5 = np.mean(m5_closes[:5])
            m5_ma20 = np.mean(m5_closes)
            m5_latest = m5_closes[0]

            # Price above 5-period MA on 5min = short-term uptrend
            if m5_latest > m5_ma5:
                score += 0.15
            else:
                score -= 0.1

            # 5min MA5 > MA20 = micro trend aligned
            if m5_ma5 > m5_ma20:
                score += 0.1

        # Check 15min: MACD trend
        if len(m15_bars) >= 26:
            m15_closes = np.array([b["close"] for b in m15_bars[:30]])
            if len(m15_closes) >= 26:
                ema12 = _ema(m15_closes[::-1], 12)  # reverse to chronological
                ema26 = _ema(m15_closes[::-1], 26)
                macd_line = ema12[-1] - ema26[-1]
                signal_line = np.mean((ema12 - ema26)[-9:])

                # MACD bullish on 15min = medium-term confirmation
                if macd_line > signal_line:
                    score += 0.15
                elif macd_line < 0:
                    score -= 0.15  # MACD negative = caution

        return max(0.3, min(score, 1.5))  # clamp between 0.3x and 1.5x

    except Exception:
        return 1.0  # neutral on any error


def scan_signals(symbol: str, name: str, klines: list) -> List[Dict[str, Any]]:
    """Scan a stock for buy signals. Returns list of triggered signal dicts."""
    closes = np.array([k["close"] for k in klines])
    highs = np.array([k["high"] for k in klines])
    lows = np.array([k["low"] for k in klines])
    volumes = np.array([k["volume"] for k in klines])
    n = len(closes)

    if n < 60:
        return []

    price = closes[-1]
    rsi = _compute_rsi(closes)
    kdj = _compute_kdj(highs, lows, closes)
    macd_div = _detect_macd_divergence(closes)

    # MA golden cross
    ma5 = np.mean(closes[-5:])
    ma20 = np.mean(closes[-20:])
    golden_cross = False
    if n >= 21:
        ma5_prev = np.mean(closes[-7:-2])
        ma20_prev = np.mean(closes[-22:-2])
        golden_cross = ma5_prev <= ma20_prev and ma5 > ma20

    # CMF
    cmf = 0.0
    if n >= 20:
        mf = ((closes[-20:] - lows[-20:]) - (highs[-20:] - closes[-20:])) / (highs[-20:] - lows[-20:] + 1e-9)
        cmf = float(np.sum(mf * volumes[-20:]) / np.sum(volumes[-20:]))

    signals = []

    # Signal 1: KDJ oversold golden cross (J < 20 AND K crosses above D)
    if kdj["golden_cross"] and kdj["j"] < 20:
        signals.append({
            "type": "KDJ超卖金叉",
            "confidence": 75,
            "detail": f"K={kdj['k']:.1f} D={kdj['d']:.1f} J={kdj['j']:.1f}",
        })

    # Signal 2: RSI oversold
    if rsi < 30:
        confidence = 60 + int((30 - rsi) * 2)  # lower RSI = higher confidence
        signals.append({
            "type": "RSI超卖",
            "confidence": min(confidence, 85),
            "detail": f"RSI={rsi:.1f}",
        })

    # Signal 3: MA golden cross
    if golden_cross:
        signals.append({
            "type": "MA金叉",
            "confidence": 65,
            "detail": f"MA5={ma5:.2f} ↑ MA20={ma20:.2f}",
        })

    # Signal 4: MACD bullish divergence
    if macd_div:
        signals.append({
            "type": "MACD底背离",
            "confidence": 70,
            "detail": "价格新低但MACD动能回升",
        })

    # Signal 5: KDJ golden cross (even without oversold J)
    if kdj["golden_cross"] and kdj["j"] >= 20:
        signals.append({
            "type": "KDJ金叉",
            "confidence": 55,
            "detail": f"K={kdj['k']:.1f} D={kdj['d']:.1f} J={kdj['j']:.1f}",
        })

    return signals


def _assess_signals(signals: List[Dict[str, Any]], price: float, name: str) -> Tuple[str, int, str]:
    """Assess signal group and generate recommendation."""
    if not signals:
        return ("NONE", 0, f"{name} 无买入信号")

    types = [s["type"] for s in signals]
    max_conf = max(s["confidence"] for s in signals)
    avg_conf = int(np.mean([s["confidence"] for s in signals]))
    n_sigs = len(signals)

    # Multi-signal convergence → strong buy
    if n_sigs >= 3:
        level = "STRONG_BUY"
        score = min(avg_conf + 15, 100)
    elif n_sigs >= 2:
        level = "BUY"
        score = min(avg_conf + 10, 95)
    elif max_conf >= 70:
        level = "BUY"
        score = max_conf
    elif max_conf >= 55:
        level = "WATCH"
        score = max_conf
    else:
        level = "WATCH"
        score = max_conf

    label = f"{'★' if level == 'STRONG_BUY' else '●' if level == 'BUY' else '○'} {name}({price:.2f})"
    return (level, score, f"{label} {'+'.join(types)} 置信度:{score}")


def push_bark(title: str, body: str, group: str = "signal") -> bool:
    """Push notification via Bark."""
    payload = {
        "device_key": settings.BARK_KEY,
        "title": title,
        "body": body,
        "badge": 1,
        "sound": "default",
        "group": group,
    }
    try:
        resp = requests.post(BARK_URL, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info(f"Bark推送成功: {title}")
            return True
        else:
            logger.warning(f"Bark推送失败: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Bark推送异常: {e}")
        return False


def _is_market_hours() -> bool:
    """Check if current time is within A-share trading hours (Mon-Fri 9:00-15:00 Beijing)."""
    from datetime import time as _time
    now = datetime.now(timezone.utc)
    bj_hour = (now.hour + 8) % 24
    bj_minute = now.minute
    bj_time = _time(bj_hour, bj_minute)
    return now.weekday() < 5 and _time(9, 0) <= bj_time <= _time(15, 5)


@celery_app.task(queue="market")
def run_signal_scanner():
    """扫描所有关注标的的买入信号，通过 Bark 推送 + 自动交易 + 数据库记录。"""
    if not _is_market_hours():
        return  # skip outside trading hours

    lock_token = acquire_sync_lock("run_signal_scanner", timeout=360)
    if not lock_token:
        return {"skipped": "another instance is running"}

    logger.info(f"信号扫描开始: {datetime.now(timezone.utc)}")

    db = SyncSessionLocal()
    try:
        # 加载所有关注的活跃 A 股标的
        symbols = db.execute(
            select(SymbolInfo).where(
                SymbolInfo.status == "active",
                SymbolInfo.asset_type == "stock",
                SymbolInfo.is_watched == True,
            )
        ).scalars().all()

        if not symbols:
            logger.info("无关注标的，跳过信号扫描")
            return

        # Bulk-load all klines in one query (much faster than N separate queries)
        symbol_list = [s.symbol for s in symbols]
        all_rows = db.execute(
            select(KLine)
            .where(KLine.symbol.in_(symbol_list), KLine.interval == "1d")
            .order_by(KLine.symbol.asc(), KLine.timestamp.asc())
        ).scalars().all()

        # Group klines by symbol
        klines_by_symbol = defaultdict(list)
        for r in all_rows:
            klines_by_symbol[r.symbol].append({
                "timestamp": int(r.timestamp.timestamp()),
                "open": r.open, "high": r.high, "low": r.low,
                "close": r.close, "volume": r.volume,
            })

        all_signals: List[Dict[str, Any]] = []
        for sym in symbols:
            klines = klines_by_symbol.get(sym.symbol, [])
            if len(klines) < 60:
                continue

            signals = scan_signals(sym.symbol, sym.name, klines)
            if signals:
                price = klines[-1]["close"]
                level, score, summary = _assess_signals(signals, price, sym.name)

                # Multi-timeframe confirmation (5min + 15min trend alignment)
                mtf_mult = confirm_multi_timeframe(sym.symbol, db)
                adjusted_score = int(score * mtf_mult)
                if mtf_mult >= 1.1 and level == "BUY":
                    level = "STRONG_BUY"  # upgrade: daily signal confirmed by short-term
                elif mtf_mult <= 0.7 and level == "STRONG_BUY":
                    level = "BUY"  # downgrade: daily signal not confirmed

                all_signals.append({
                    "symbol": sym.symbol,
                    "name": sym.name,
                    "price": price,
                    "level": level,
                    "score": adjusted_score,
                    "mtf_mult": round(mtf_mult, 2),
                    "summary": summary,
                    "signals": signals,
                })

        if not all_signals:
            logger.info("信号扫描完成: 无买入信号")
            return

        # 按置信度排序，强买入优先
        all_signals.sort(key=lambda x: (x["level"] != "STRONG_BUY", x["level"] != "BUY", -x["score"]))

        # 构建推送消息
        strong_buys = [s for s in all_signals if s["level"] == "STRONG_BUY"]
        buys = [s for s in all_signals if s["level"] == "BUY"]
        watches = [s for s in all_signals if s["level"] == "WATCH"]

        lines = []
        if strong_buys:
            lines.append("🔥 强买入信号:")
            for s in strong_buys:
                lines.append(f"  ★ {s['name']}({s['price']:.2f}) 评分:{s['score']}")
                for sig in s["signals"]:
                    lines.append(f"    - {sig['type']}: {sig['detail']}")
        if buys:
            if lines:
                lines.append("")
            lines.append("📈 买入信号:")
            for s in buys:
                lines.append(f"  ● {s['name']}({s['price']:.2f}) 评分:{s['score']}")
                for sig in s["signals"]:
                    lines.append(f"    - {sig['type']}: {sig['detail']}")
        if watches:
            if lines:
                lines.append("")
            lines.append("👀 关注信号:")
            for s in watches[:3]:
                lines.append(f"  ○ {s['name']}({s['price']:.2f}) 评分:{s['score']}")

        body = "\n".join(lines)
        now_str = datetime.now().strftime("%H:%M")

        # Bark push
        if strong_buys or buys:
            title = f"🔥 买入信号 {now_str}" if strong_buys else f"📈 交易信号 {now_str}"
            push_bark(title, body)
        elif watches:
            push_bark(f"👀 关注信号 {now_str}", body)

        # 记录到数据库 Notification（user_id=1 作为系统默认）
        for s in all_signals:
            if s["level"] in ("STRONG_BUY", "BUY"):
                notif = Notification(
                    user_id=1,
                    type="trade",
                    title=f"{s['level']}: {s['name']}",
                    content=s["summary"],
                    metadata_json={
                        "symbol": s["symbol"],
                        "price": s["price"],
                        "score": s["score"],
                        "signals": s["signals"],
                    },
                )
                db.add(notif)

        db.commit()

        # ── 自动交易引擎 ──
        if settings.AUTO_TRADE_ENABLED:
            try:
                from app.services.auto_trade import execute_signal_batch_sync
                auto_results = execute_signal_batch_sync(db, user_id=1, stock_signals=all_signals)
                auto_count = sum(1 for r in auto_results if r["auto_trade"]["executed"])
                if auto_count > 0:
                    logger.info(f"自动交易完成: {auto_count} 笔")
                    for r in auto_results:
                        if r["auto_trade"]["executed"]:
                            at = r["auto_trade"]
                            mode = "[DRY-RUN]" if at.get("dry_run") else "[LIVE]"
                            logger.info(f"  {mode} {r['name']} {at['reason']}")
            except Exception as e:
                logger.error(f"自动交易异常: {e}", exc_info=True)

        logger.info(f"信号扫描完成: {len(all_signals)} 个信号 (强买{len(strong_buys)} 买入{len(buys)} 关注{len(watches)})")

    except Exception as e:
        db.rollback()
        logger.error(f"信号扫描异常: {e}", exc_info=True)
        raise
    finally:
        db.close()
        release_sync_lock("run_signal_scanner", lock_token)
