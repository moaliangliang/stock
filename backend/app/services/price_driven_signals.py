"""
Price-driven signal checker — called from WebSocket broadcast loop after ticker refresh.

When prices change, this immediately scans for buy signals and triggers auto-trade,
eliminating the 0-30s Celery polling delay. Runs sync DB work in a thread executor.
"""
import asyncio
import time as _time_module
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from loguru import logger

from app.core.config import settings

# Dedup: track last scan time per symbol to avoid redundant scans within short window
_last_scan: Dict[str, float] = {}
_SCAN_COOLDOWN = 8  # seconds — slightly less than the 10s broadcast interval


def _run_signal_check_for_symbols(symbols_to_check: List[tuple]):
    """
    Run signal scanning for specific symbols in a sync DB session.
    Runs in thread executor — safe to call from async context.

    Each tuple: (symbol, name)
    """
    from collections import defaultdict
    import numpy as np

    from app.core.database import SyncSessionLocal
    from sqlalchemy import select
    from app.models.market_data import KLine, SymbolInfo
    from app.models.notification import Notification
    from app.tasks.signal_scanner import scan_signals, _assess_signals, confirm_multi_timeframe
    from app.utils.notify import push_bark

    if not symbols_to_check:
        return

    # ── Dedup: skip if Celery scanner ran within the last 30s ──
    try:
        import json as _json
        import datetime as _dt
        from app.core.redis import get_sync_redis
        r = get_sync_redis()
        cached = r.get("signal_scan:latest")
        if cached:
            cached_ts = _json.loads(cached).get("ts", "")
            if cached_ts:
                cached_dt = _dt.datetime.fromisoformat(cached_ts)
                age = (_dt.datetime.now(_dt.timezone.utc) - cached_dt).total_seconds()
                if age < 30:
                    return  # Celery scan is fresh, skip duplicate work
    except Exception:
        pass

    db = SyncSessionLocal()
    try:
        t0 = _time_module.time()
        symbols = [s[0] for s in symbols_to_check]
        names = {s[0]: s[1] for s in symbols_to_check}

        # ── Stage 1: Bulk load K-lines ──
        all_rows = db.execute(
            select(KLine)
            .where(KLine.symbol.in_(symbols), KLine.interval == "1d")
            .order_by(KLine.symbol.asc(), KLine.timestamp.asc())
        ).scalars().all()

        klines_by_symbol = defaultdict(list)
        for r in all_rows:
            klines_by_symbol[r.symbol].append({
                "timestamp": int(r.timestamp.timestamp()),
                "open": r.open, "high": r.high, "low": r.low,
                "close": r.close, "volume": r.volume,
            })
        t1 = _time_module.time()

        # ── Stage 2: Scan signals + MTF confirmation ──
        all_signals: List[Dict[str, Any]] = []
        for symbol in symbols:
            klines = klines_by_symbol.get(symbol, [])
            if len(klines) < 60:
                continue

            name = names.get(symbol, symbol)
            signals = scan_signals(symbol, name, klines)
            if not signals:
                continue

            price = klines[-1]["close"]
            level, score, summary = _assess_signals(signals, price, name)

            # Backtest gate
            if level in ("STRONG_BUY", "BUY"):
                from app.services.backtest import filter_signals_by_backtest
                filtered, gate = filter_signals_by_backtest(signals, symbol)
                if not gate["passed"]:
                    if gate.get("best_strategy"):
                        level = "WATCH"
                        score = min(score, 40)
                        summary = f"{summary} [回测门禁: {gate['reason']}]"
                    else:
                        continue

            mtf_mult = confirm_multi_timeframe(symbol, db)
            adjusted_score = int(score * mtf_mult)
            if mtf_mult >= 1.1 and level == "BUY":
                level = "STRONG_BUY"
            elif mtf_mult <= 0.7 and level == "STRONG_BUY":
                level = "BUY"

            all_signals.append({
                "symbol": symbol,
                "name": name,
                "price": price,
                "level": level,
                "score": adjusted_score,
                "mtf_mult": round(mtf_mult, 2),
                "summary": summary,
                "signals": signals,
            })
        t2 = _time_module.time()

        if not all_signals:
            logger.info(f"[PD-TIMING] {len(symbols)} stocks, K-lines={t1-t0:.2f}s scan={t2-t1:.2f}s total={t2-t0:.2f}s (no signals)")
            return

        all_signals.sort(key=lambda x: (x["level"] != "STRONG_BUY", x["level"] != "BUY", -x["score"]))

        strong_buys = [s for s in all_signals if s["level"] == "STRONG_BUY"]
        buys = [s for s in all_signals if s["level"] == "BUY"]
        watches = [s for s in all_signals if s["level"] == "WATCH"]

        # ── Stage 3: Bark push ──
        lines = []
        if strong_buys:
            lines.append("[Price-Driven] Strong Buy:")
            for s in strong_buys:
                lines.append(f"  * {s['name']}({s['price']:.2f}) score:{s['score']}")
                for sig in s["signals"]:
                    lines.append(f"    - {sig['type']}: {sig['detail']}")
        if buys:
            if lines:
                lines.append("")
            lines.append("[Price-Driven] Buy:")
            for s in buys:
                lines.append(f"  * {s['name']}({s['price']:.2f}) score:{s['score']}")
                for sig in s["signals"]:
                    lines.append(f"    - {sig['type']}: {sig['detail']}")

        body = "\n".join(lines)
        now_str = datetime.now().strftime("%H:%M:%S")

        if strong_buys or buys:
            title = f"Price-Driven Buy {now_str}" if strong_buys else f"Price-Driven Signal {now_str}"
            push_bark(title, body)
        t3 = _time_module.time()

        # ── Stage 4: Save to DB ──
        for s in all_signals:
            if s["level"] in ("STRONG_BUY", "BUY"):
                notif = Notification(
                    user_id=1,
                    type="trade",
                    title=f"[PD] {s['level']}: {s['name']}",
                    content=s["summary"],
                    metadata_json={
                        "symbol": s["symbol"],
                        "price": s["price"],
                        "score": s["score"],
                        "signals": s["signals"],
                        "source": "price_driven",
                    },
                )
                db.add(notif)

        db.commit()
        t4 = _time_module.time()

        # ── Stage 5: Auto-trade ──
        auto_count = 0
        t5 = t4
        if settings.AUTO_TRADE_ENABLED:
            try:
                from app.services.auto_trade import execute_signal_batch_sync
                auto_results = execute_signal_batch_sync(db, user_id=1, stock_signals=all_signals)
                t5 = _time_module.time()
                auto_count = sum(1 for r in auto_results if r["auto_trade"]["executed"])
                if auto_count > 0:
                    logger.info(f"[PRICE-DRIVEN] Auto-trade: {auto_count} orders")
                    for r in auto_results:
                        if r["auto_trade"]["executed"]:
                            at = r["auto_trade"]
                            logger.info(f"  {at['reason']}")
            except Exception as e:
                t5 = _time_module.time()
                logger.error(f"[PRICE-DRIVEN] Auto-trade error: {e}", exc_info=True)

        # ── Summary timing ──
        logger.info(
            f"[PD-TIMING] {len(symbols)} stocks → {len(all_signals)} signals "
            f"(strong:{len(strong_buys)} buy:{len(buys)}) "
            f"| K-lines={t1-t0:.2f}s scan+MTF={t2-t1:.2f}s "
            f"bark={t3-t2:.2f}s db={t4-t3:.2f}s "
            f"auto-trade={t5-t4:.2f}s | TOTAL={t5-t0:.2f}s"
        )

    except Exception as e:
        db.rollback()
        logger.error(f"[PRICE-DRIVEN] Signal check failed: {e}", exc_info=True)
    finally:
        db.close()


async def check_signals_for_changed(symbols: List[tuple]):
    """
    Async entry point: run signal checks in thread executor for symbols whose prices changed.

    Args:
        symbols: list of (symbol, name) tuples
    """
    if not symbols:
        return

    now = datetime.now(timezone.utc).timestamp()
    filtered = []
    for sym, name in symbols:
        last = _last_scan.get(sym, 0)
        if now - last >= _SCAN_COOLDOWN:
            _last_scan[sym] = now
            filtered.append((sym, name))

    if not filtered:
        return

    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _run_signal_check_for_symbols, filtered)
    except Exception as e:
        logger.error(f"[PRICE-DRIVEN] Executor failed: {e}", exc_info=True)
